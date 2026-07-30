from __future__ import annotations

import logging
import os
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.config import CameraConfig, Settings
from app.database import Database
from app.recorder.ffmpeg import command, probe
from app.logging_setup import redact
from app.storage.mounts import validate_mount

LOG = logging.getLogger(__name__)


def next_boundary(now: datetime, timezone) -> datetime:
    local = now.astimezone(timezone)
    if local.hour < 12:
        target = local.replace(hour=12, minute=0, second=0, microsecond=0)
    else:
        target = (local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return target.astimezone(UTC)


class RecorderWorker:
    def __init__(self, settings: Settings, db: Database, camera: CameraConfig):
        self.settings, self.db, self.camera = settings, db, camera
        self.stop_requested = False
        self._process: subprocess.Popen | None = None

    def request_stop(self) -> None:
        self.stop_requested = True
        if self._process and self._process.poll() is None:
            self._process.terminate()

    def _select_mount(self):
        candidates = (self.settings.recording_a, self.settings.recording_b)
        for config in candidates:
            status = validate_mount(config, self.settings.expected_segment_bytes)
            if status.available and status.writable and not status.reason:
                return config
            self.db.health("storage", "critical", f"{config.name} unavailable: {status.reason}")
        return None

    def run(self) -> None:
        delay = self.camera.reconnect_initial
        while not self.stop_requested:
            mount = self._select_mount()
            if not mount:
                time.sleep(min(delay, self.camera.reconnect_max)); delay = min(delay * 2, self.camera.reconnect_max); continue
            start = datetime.now(UTC)
            boundary = next_boundary(start, self.settings.timezone)
            seconds = max(1, boundary.timestamp() - start.timestamp())
            stamp = start.astimezone(self.settings.timezone).strftime("%Y%m%dT%H%M%S%z")
            work = mount.path / "in_progress" / self.camera.id
            final = mount.path / "clips" / self.camera.id
            status = validate_mount(mount, self.settings.expected_segment_bytes)
            if not (status.available and status.writable and not status.reason):
                time.sleep(delay); continue
            work.mkdir(parents=True, exist_ok=True); final.mkdir(parents=True, exist_ok=True)
            temp = work / f"{self.camera.id}_{stamp}.mkv.part"
            published = final / f"{self.camera.id}_{stamp}.mkv"
            clip_id = self.db.create_clip(self.camera.id, temp, mount.name, start, str(self.settings.timezone))
            LOG.info("starting recording camera=%s clip=%s", self.camera.id, clip_id)
            try:
                self._process = subprocess.Popen(command(self.settings, self.camera, temp, seconds), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
                try:
                    _stdout, stderr = self._process.communicate(timeout=seconds + 120)
                    returncode = self._process.returncode
                finally:
                    self._process = None
                if stderr:
                    LOG.warning("ffmpeg camera=%s exited=%s: %s", self.camera.id, returncode, redact(stderr[-1000:]))
                end = datetime.now(UTC)
                if temp.exists() and temp.stat().st_size > 0 and probe(self.settings, temp):
                    os.replace(temp, published)
                    with self.db.connect() as con:
                        con.execute("UPDATE clips SET recording_path=?,updated_at=? WHERE id=?", (str(published), end.isoformat(), clip_id))
                    self.db.complete_clip(clip_id, end, published.stat().st_size, "Finalized" if returncode == 0 else "Interrupted")
                    delay = self.camera.reconnect_initial
                else:
                    self.db.complete_clip(clip_id, end, 0, "Interrupted")
                    self.db.health(f"camera:{self.camera.id}", "error", "ffmpeg failed before a recoverable clip was created")
                    time.sleep(delay); delay = min(delay * 2, self.camera.reconnect_max)
            except (subprocess.TimeoutExpired, OSError) as exc:
                self.db.health(f"camera:{self.camera.id}", "error", f"recorder failure: {exc}")
                time.sleep(delay); delay = min(delay * 2, self.camera.reconnect_max)
