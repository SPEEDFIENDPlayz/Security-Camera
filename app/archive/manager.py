from __future__ import annotations

import logging
import os
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path

from app.config import Settings
from app.database import Database
from app.recorder.ffmpeg import probe
from app.storage.mounts import MountError, safe_child, validate_mount

LOG = logging.getLogger(__name__)


class ArchiveWorker:
    def __init__(self, settings: Settings, db: Database):
        self.settings, self.db = settings, db

    def _retry(self, job, token: str, error: Exception | str) -> None:
        seconds = min(3600, 15 * (2 ** min(job["attempt"], 8)))
        self.db.transition_job(job["id"], token, "Retry waiting", error=str(error), retry_seconds=seconds)
        self.db.health("archive", "error", str(error))

    def process_archive(self, job, token: str) -> None:
        clip = self.db.get_clip(job["clip_id"])
        if not clip:
            self.db.transition_job(job["id"], token, "Failed", error="source clip no longer exists"); return
        if not clip["recording_path"]:
            if clip["archive_path"]:
                try:
                    archive_status = validate_mount(self.settings.archive, require_writable=False, enforce_reserve=False)
                    published = safe_child(self.settings.archive.path, Path(clip["archive_path"]))
                    if archive_status.available and not archive_status.reason and published.exists() and published.stat().st_size > 0 and probe(self.settings, published):
                        self.db.transition_job(job["id"], token, "Complete")
                        return
                except MountError:
                    pass
            self.db.transition_job(job["id"], token, "Failed", error="source clip no longer exists"); return
        try:
            source = safe_child(self.settings.recording.path, Path(clip["recording_path"]))
        except MountError as exc:
            self.db.transition_job(job["id"], token, "Failed", error=str(exc)); return
        required = int(clip["size_bytes"] or 0)
        if not required and source.exists():
            required = source.stat().st_size
        status = validate_mount(self.settings.archive, required)
        if not (status.available and status.writable and not status.reason):
            self._retry(job, token, f"archive mount unavailable: {status.reason}"); return
        root = self.settings.archive.path
        final = root / "clips" / f"{clip['id']}.mkv"
        temp = root / "tmp" / f"{clip['id']}.mkv.partial"
        try:
            safe_child(root, final); safe_child(root, temp)
        except MountError as exc:
            self.db.transition_job(job["id"], token, "Failed", error=str(exc)); return
        if not source.exists():
            if final.exists() and final.stat().st_size > 0 and probe(self.settings, final):
                with self.db.immediate() as con:
                    con.execute("UPDATE clips SET state='Archived locally',archive_path=?,recording_path=NULL,protected=1,updated_at=? WHERE id=?", (str(final), datetime.now(UTC).isoformat(), clip["id"]))
                self.db.transition_job(job["id"], token, "Complete")
            else:
                self._retry(job, token, "source file missing; preserving metadata for operator review")
            return
        source_status = validate_mount(self.settings.recording, require_writable=False, enforce_reserve=False)
        if not source_status.available or source_status.reason:
            self._retry(job, token, f"recording mount unavailable: {source_status.reason}"); return
        if not self.db.transition_job(job["id"], token, "Copying"):
            return
        try:
            final.parent.mkdir(parents=True, exist_ok=True); temp.parent.mkdir(parents=True, exist_ok=True)
            with source.open("rb") as src, temp.open("wb") as dst:
                shutil.copyfileobj(src, dst, length=4 * 1024 * 1024)
                dst.flush(); os.fsync(dst.fileno())
            if not self.db.transition_job(job["id"], token, "Verifying"):
                return
            if source.stat().st_size != temp.stat().st_size:
                raise IOError("archive copy size mismatch")
            if not probe(self.settings, temp):
                raise IOError("ffprobe rejected archive copy")
            if not self.db.transition_job(job["id"], token, "Publishing"):
                return
            os.replace(temp, final)
            directory_fd = os.open(final.parent, os.O_DIRECTORY)
            try: os.fsync(directory_fd)
            finally: os.close(directory_fd)
            with self.db.immediate() as con:
                con.execute("UPDATE clips SET state='Archived locally',archive_path=?,updated_at=? WHERE id=?", (str(final), datetime.now(UTC).isoformat(), clip["id"]))
            if not self.db.transition_job(job["id"], token, "Source deletion pending"):
                return
            delete_status = validate_mount(self.settings.recording, require_writable=True, enforce_reserve=False)
            if not (delete_status.available and delete_status.writable and not delete_status.reason):
                self._retry(job, token, f"recording mount unavailable for source deletion: {delete_status.reason}"); return
            safe_child(self.settings.recording.path, source)
            source.unlink()
            with self.db.immediate() as con:
                con.execute("UPDATE clips SET recording_path=NULL,protected=1,updated_at=? WHERE id=?", (datetime.now(UTC).isoformat(), clip["id"]))
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
        if not clip:
            self.db.transition_job(job["id"], token, "Failed", error="archive path unavailable"); return
        if not clip["archive_path"] and clip["state"] == "Deleted":
            self.db.transition_job(job["id"], token, "Complete"); return
        if not clip["archive_path"]:
            self.db.transition_job(job["id"], token, "Failed", error="archive path unavailable"); return
        try:
            status = validate_mount(self.settings.archive, require_writable=True, enforce_reserve=False)
            if not (status.available and status.writable and not status.reason):
                raise MountError(status.reason)
            path = safe_child(self.settings.archive.path, Path(clip["archive_path"]))
            path.unlink(missing_ok=True)
            with self.db.connect() as con:
                con.execute("UPDATE clips SET state='Deleted',archive_path=NULL,deleted_at=?,updated_at=? WHERE id=?", (datetime.now(UTC).isoformat(), datetime.now(UTC).isoformat(), clip["id"]))
            self.db.transition_job(job["id"], token, "Complete")
        except Exception as exc:
            self._retry(job, token, exc)

    def loop(self) -> None:
        while True:
            found = False
            for kind, method in (("archive", self.process_archive), ("delete_archive", self.process_delete)):
                job = self.db.claim(kind, "archive")
                if job:
                    found = True; method(job, job["lease_token"])
            if not found: time.sleep(2)
