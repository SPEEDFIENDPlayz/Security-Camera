from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

from app.config import Settings
from app.database import Database
from app.recorder.ffmpeg import probe


def reconcile(settings: Settings, db: Database) -> int:
    """Recover known in-progress recordings conservatively; never delete unknown files."""
    recovered = 0
    with db.connect() as con:
        rows = con.execute("SELECT * FROM clips WHERE state IN ('Recording','Finalizing','Archive copying','Archive verifying')").fetchall()
    for clip in rows:
        if clip["state"] in {"Recording", "Finalizing"} and clip["recording_path"]:
            path = Path(clip["recording_path"])
            if path.exists() and path.stat().st_size and probe(settings, path):
                target = path.with_suffix("") if path.suffix == ".part" else path
                if target != path: os.replace(path, target)
                with db.connect() as con:
                    con.execute("UPDATE clips SET state='Interrupted',recording_path=?,ended_at=?,size_bytes=?,updated_at=? WHERE id=?", (str(target), datetime.now(UTC).isoformat(), target.stat().st_size, datetime.now(UTC).isoformat(), clip["id"]))
                recovered += 1
            else:
                db.health("reconciliation", "warning", f"clip {clip['id']} needs manual inspection")
        else:
            db.health("reconciliation", "warning", f"archive operation {clip['id']} requires job reconciliation")
    return recovered
