from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.config import Settings
from app.database import Database
from app.storage.mounts import safe_child, validate_mount

LOG = logging.getLogger(__name__)


def run_retention(settings: Settings, db: Database) -> int:
    before = datetime.now(UTC) - timedelta(hours=settings.retention_hours)
    removed = 0
    status = validate_mount(settings.recording, require_writable=True, enforce_reserve=False)
    if not (status.available and status.writable and not status.reason):
        db.health("retention", "warning", f"recording mount unavailable: {status.reason}")
        return 0
    root = settings.recording.path.resolve()
    for clip in db.eligible_for_retention(before):
        path = Path(clip["recording_path"])
        try:
            safe_child(root, path)
            status = validate_mount(settings.recording, require_writable=True, enforce_reserve=False)
            if not (status.available and status.writable and not status.reason):
                db.health("retention", "warning", f"recording mount became unavailable: {status.reason}")
                break
            with db.immediate() as con:
                claimed = con.execute("UPDATE clips SET state='Delete pending',updated_at=? WHERE id=? AND state IN ('Finalized','Interrupted','Delete pending') AND protected=0", (datetime.now(UTC).isoformat(), clip["id"])).rowcount
            if not claimed:
                continue
            if path.exists():
                path.unlink()
            with db.connect() as con:
                con.execute("UPDATE clips SET state='Deleted',recording_path=NULL,deleted_at=?,updated_at=? WHERE id=? AND state='Delete pending' AND protected=0", (datetime.now(UTC).isoformat(), datetime.now(UTC).isoformat(), clip["id"]))
            removed += 1
        except Exception as exc:
            LOG.exception("retention failed for clip %s: %s", clip["id"], exc)
            db.health("retention", "error", f"retention failed for {clip['id']}: {exc}")
    return removed
