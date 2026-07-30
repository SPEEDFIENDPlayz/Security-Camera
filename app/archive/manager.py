from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path

from app.archive.google_drive import DriveUploader, UploadCancelled
from app.config import Settings
from app.database import Database
from app.recorder.ffmpeg import probe
from app.storage.mounts import MountError, safe_child, validate_mount

LOG = logging.getLogger(__name__)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class ArchiveWorker:
    def __init__(self, settings: Settings, db: Database):
        self.settings, self.db = settings, db

    def _retry(self, job, token: str, error: Exception | str) -> None:
        seconds = min(3600, 15 * (2 ** min(job["attempt"], 8)))
        self.db.transition_job(job["id"], token, "Retry waiting", error=str(error), retry_seconds=seconds)
        self.db.health("archive", "error", str(error))

    def process_archive(self, job, token: str) -> None:
        clip = self.db.get_clip(job["clip_id"])
        if not clip or not clip["recording_path"]:
            self.db.transition_job(job["id"], token, "Failed", error="source clip no longer exists"); return
        source = Path(clip["recording_path"])
        required = int(clip["size_bytes"] or source.stat().st_size if source.exists() else 0)
        status = validate_mount(self.settings.archive, required)
        if not (status.available and status.writable and not status.reason):
            self._retry(job, token, f"archive mount unavailable: {status.reason}"); return
        if not source.exists():
            self._retry(job, token, "source file missing; preserving metadata for operator review"); return
        root = self.settings.archive.path
        final = root / "clips" / f"{clip['id']}.mkv"
        temp = root / "tmp" / f"{clip['id']}.mkv.partial"
        self.db.transition_job(job["id"], token, "Copying")
        try:
            final.parent.mkdir(parents=True, exist_ok=True); temp.parent.mkdir(parents=True, exist_ok=True)
            safe_child(root, final); safe_child(root, temp)
            with source.open("rb") as src, temp.open("wb") as dst:
                shutil.copyfileobj(src, dst, length=4 * 1024 * 1024)
                dst.flush(); os.fsync(dst.fileno())
            self.db.transition_job(job["id"], token, "Verifying")
            if source.stat().st_size != temp.stat().st_size:
                raise IOError("archive copy size mismatch")
            if not probe(self.settings, temp):
                raise IOError("ffprobe rejected archive copy")
            if bool(self.settings.google.get("checksum", False)) and _sha256(source) != _sha256(temp):
                raise IOError("archive checksum mismatch")
            self.db.transition_job(job["id"], token, "Publishing")
            os.replace(temp, final)
            directory_fd = os.open(final.parent, os.O_DIRECTORY)
            try: os.fsync(directory_fd)
            finally: os.close(directory_fd)
            with self.db.immediate() as con:
                con.execute("UPDATE clips SET state='Archived locally',archive_path=?,updated_at=? WHERE id=?", (str(final), datetime.now(UTC).isoformat(), clip["id"]))
            self.db.transition_job(job["id"], token, "Source deletion pending")
            source.unlink()
            with self.db.immediate() as con:
                con.execute("UPDATE clips SET recording_path=NULL,protected=1,updated_at=? WHERE id=?", (datetime.now(UTC).isoformat(), clip["id"]))
                con.execute("INSERT INTO jobs(id,kind,clip_id,state,created_at,updated_at) VALUES(?,?,?,?,?,?)", (f"upload:{clip['id']}", "upload", clip["id"], "Queued", datetime.now(UTC).isoformat(), datetime.now(UTC).isoformat()))
            self.db.transition_job(job["id"], token, "Complete")
        except Exception as exc:
            LOG.exception("archive transfer failed for %s", clip["id"])
            if temp.exists():
                quarantine = root / "quarantine" / f"{clip['id']}.{int(time.time())}.partial"
                try:
                    quarantine.parent.mkdir(parents=True, exist_ok=True); os.replace(temp, quarantine)
                except OSError:
                    temp.unlink(missing_ok=True)
            self._retry(job, token, exc)

    def process_delete(self, job, token: str) -> None:
        clip = self.db.get_clip(job["clip_id"])
        if not clip or not clip["archive_path"]:
            self.db.transition_job(job["id"], token, "Failed", error="archive path unavailable"); return
        try:
            with self.db.connect() as con:
                active = con.execute("SELECT 1 FROM jobs WHERE kind='upload' AND clip_id=? AND state='Cancelled' AND lease_until>?", (clip["id"], datetime.now(UTC).isoformat())).fetchone()
            if active:
                self.db.transition_job(job["id"], token, "Retry waiting", error="waiting for upload reader to release file", retry_seconds=5)
                return
            status = validate_mount(self.settings.archive)
            if not (status.available and status.writable and not status.reason):
                raise MountError(status.reason)
            path = safe_child(self.settings.archive.path, Path(clip["archive_path"]))
            path.unlink(missing_ok=True)
            with self.db.connect() as con:
                con.execute("UPDATE clips SET state='Deleted',archive_path=NULL,deleted_at=?,updated_at=? WHERE id=?", (datetime.now(UTC).isoformat(), datetime.now(UTC).isoformat(), clip["id"]))
            self.db.transition_job(job["id"], token, "Complete")
        except Exception as exc:
            self._retry(job, token, exc)

    def process_upload(self, job, token: str) -> None:
        clip = self.db.get_clip(job["clip_id"])
        if not clip or not clip["archive_path"]:
            self.db.transition_job(job["id"], token, "Cancelled", error="archive copy unavailable"); return
        if not self.settings.google.get("enabled", False):
            self.db.transition_job(job["id"], token, "Retry waiting", error="Google Drive disabled", retry_seconds=3600); return
        try:
            self.db.transition_job(job["id"], token, "Uploading")
            result = DriveUploader(self.settings, self.db).upload(job, clip)
            if not self.db.owns_job(job["id"], token):
                return
            with self.db.connect() as con:
                con.execute("UPDATE clips SET state='Uploaded verified',drive_file_id=?,drive_size_bytes=?,drive_md5=?,updated_at=? WHERE id=?", (result["id"], result.get("size"), result.get("md5Checksum"), datetime.now(UTC).isoformat(), clip["id"]))
            self.db.transition_job(job["id"], token, "Complete", payload=result)
        except UploadCancelled:
            # Delete request owns final cleanup. Do not touch clip state or revive this job.
            return
        except Exception as exc:
            self._retry(job, token, exc)

    def loop(self) -> None:
        while True:
            found = False
            for kind, method in (("archive", self.process_archive), ("delete_archive", self.process_delete), ("upload", self.process_upload)):
                job = self.db.claim(kind, "archive")
                if job:
                    found = True; method(job, job["lease_token"])
            if not found: time.sleep(2)
