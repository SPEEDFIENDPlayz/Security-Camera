from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.config import Settings
from app.database import Database
from app.storage.mounts import safe_child

LOG = logging.getLogger(__name__)


def run_retention(settings: Settings, db: Database) -> int:
    before = datetime.now(UTC) - timedelta(hours=settings.retention_hours)
    removed = 0
    roots = {str(settings.recording.path.resolve())}
    for clip in db.eligible_for_retention(before):
        path = Path(clip["recording_path"])
        try:
            root = next(Path(item) for item in roots if path.resolve().is_relative_to(Path(item)))
            safe_child(root, path)
            with db.immediate() as con:
                claimed = con.execute("UPDATE clips SET state='Delete pending',updated_at=? WHERE id=? AND state IN ('Finalized','Interrupted') AND protected=0", (datetime.now(UTC).isoformat(), clip["id"])).rowcount
            if not claimed:
                continue
            if path.exists():
                path.unlink()
            with db.connect() as con:
                con.execute("UPDATE clips SET state='Deleted',recording_path=NULL,deleted_at=?,updated_at=? WHERE id=?", (datetime.now(UTC).isoformat(), datetime.now(UTC).isoformat(), clip["id"]))
            removed += 1
        except Exception as exc:
            LOG.exception("retention failed for clip %s: %s", clip["id"], exc)
            db.health("retention", "error", f"retention failed for {clip['id']}: {exc}")
    return removed
