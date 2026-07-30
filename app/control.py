from __future__ import annotations

import os
import shutil
import subprocess
import time
import grp
from pathlib import Path

from app.config import ConfigError, load
from app.database import Database


class ControlWorker:
    """Applies validated configuration revisions submitted as database jobs.

    The dashboard only queues a candidate path; this worker is the sole owner of
    the active TOML replacement.  Service reload is intentionally signal based,
    so it works without granting the web process systemctl privileges.
    """
    def __init__(self, config_path: Path, db: Database):
        self.config_path, self.db = config_path, db

    def loop(self) -> None:
        while True:
            job = self.db.claim("config", "control")
            if not job:
                time.sleep(2); continue
            token = job["lease_token"]
            previous: Path | None = None
            applied = False
            try:
                candidate = Path(__import__("json").loads(job["payload"])["candidate"])
                load(candidate)  # validates complete candidate before touching active file
                previous = self.config_path.with_suffix(".toml.previous")
                temporary = self.config_path.with_suffix(".toml.next")
                previous_settings = load(self.config_path)
                candidate_settings = load(candidate)
                if (previous_settings.recording_a != candidate_settings.recording_a or previous_settings.recording_b != candidate_settings.recording_b or previous_settings.archive != candidate_settings.archive):
                    raise ConfigError("storage mount changes require a controlled maintenance migration and are rejected while active")
                shutil.copyfile(candidate, temporary)
                with temporary.open("rb") as stream: os.fsync(stream.fileno())
                os.chmod(temporary, 0o640)
                os.chown(temporary, 0, grp.getgrnam("securitycam").gr_gid)
                if self.config_path.exists(): shutil.copy2(self.config_path, previous)
                os.replace(temporary, self.config_path)
                applied = True
                try: load(self.config_path)
                except Exception:
                    os.replace(previous, self.config_path); raise
                # Camera reload is granular in the recorder; archive/dashboard
                # restart independently without interrupting FFmpeg workers.
                subprocess.run(["/bin/systemctl", "reload", "security-camera-recorder.service"], check=True, timeout=30)
                subprocess.run(["/bin/systemctl", "try-restart", "security-camera-archive.service", "security-camera-dashboard.service"], check=True, timeout=30)
                candidate.unlink(missing_ok=True)
                self.db.transition_job(job["id"], token, "Complete")
            except Exception as exc:
                if applied and previous and previous.exists():
                    try:
                        os.replace(previous, self.config_path)
                        subprocess.run(["/bin/systemctl", "reload", "security-camera-recorder.service"], check=False, timeout=30)
                        subprocess.run(["/bin/systemctl", "try-restart", "security-camera-archive.service", "security-camera-dashboard.service"], check=False, timeout=30)
                    except Exception:
                        self.db.health("control", "critical", "configuration rollback itself failed; manual intervention required")
                self.db.transition_job(job["id"], token, "Failed", error=str(exc))
                self.db.health("control", "error", f"configuration rejected: {exc}")
