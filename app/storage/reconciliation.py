from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

from app.config import Settings
from app.database import Database
from app.recorder.ffmpeg import probe
from app.storage.mounts import safe_child, validate_mount


def reconcile(settings: Settings, db: Database) -> int:
    """Recover known in-progress recordings conservatively; never delete unknown files."""
    recovered = 0
    with db.connect() as con:
        rows = con.execute("SELECT * FROM clips WHERE state IN ('Recording','Finalizing','Archive copying','Archive verifying')").fetchall()
    recording_root = settings.recording.path.resolve()
    for clip in rows:
        if clip["state"] in {"Recording", "Finalizing"} and clip["recording_path"]:
            path = Path(clip["recording_path"])
            status = validate_mount(settings.recording, require_writable=False, enforce_reserve=False)
            try:
                safe_child(recording_root, path)
            except Exception as exc:
                db.health("reconciliation", "error", f"clip {clip['id']} path rejected: {exc}")
                continue
            candidate = path
            if not candidate.exists() and path.suffix == ".part":
                candidate = path.with_suffix("")
            if not status.available or not status.writable or status.reason or not candidate.exists():
                reason = status.reason or "recording file missing"
                db.health("reconciliation", "warning", f"clip {clip['id']} cannot be recovered: {reason}")
                continue
            if candidate.stat().st_size and probe(settings, candidate):
                target = candidate.with_suffix("") if candidate.suffix == ".part" else candidate
                if target != candidate: os.replace(candidate, target)
                with db.connect() as con:
                    con.execute("UPDATE clips SET state='Interrupted',recording_path=?,ended_at=?,size_bytes=?,updated_at=? WHERE id=?", (str(target), datetime.now(UTC).isoformat(), target.stat().st_size, datetime.now(UTC).isoformat(), clip["id"]))
                recovered += 1
            else:
                db.health("reconciliation", "warning", f"clip {clip['id']} needs manual inspection")
        else:
            db.health("reconciliation", "warning", f"archive operation {clip['id']} requires job reconciliation")
    return recovered
