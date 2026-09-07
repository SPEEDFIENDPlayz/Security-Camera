from __future__ import annotations

import contextlib
import json
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator


def utcnow() -> str:
    return datetime.now(UTC).isoformat()


SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS clips (
 id TEXT PRIMARY KEY, camera_id TEXT NOT NULL, state TEXT NOT NULL,
 recording_path TEXT, archive_path TEXT, storage_name TEXT,
 started_at TEXT NOT NULL, ended_at TEXT, local_timezone TEXT NOT NULL,
 size_bytes INTEGER, protected INTEGER NOT NULL DEFAULT 0,
 drive_file_id TEXT, drive_size_bytes INTEGER, drive_md5 TEXT,
 deleted_at TEXT, last_error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS clips_state_end ON clips(state, ended_at);
CREATE TABLE IF NOT EXISTS jobs (
 id TEXT PRIMARY KEY, kind TEXT NOT NULL, clip_id TEXT REFERENCES clips(id), state TEXT NOT NULL,
 payload TEXT NOT NULL DEFAULT '{}', attempt INTEGER NOT NULL DEFAULT 0, not_before TEXT,
 lease_token TEXT, lease_until TEXT, error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_claim ON jobs(kind, state, not_before, lease_until);
CREATE TABLE IF NOT EXISTS health_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, component TEXT NOT NULL, severity TEXT NOT NULL,
 message TEXT NOT NULL, created_at TEXT NOT NULL, resolved_at TEXT
);
CREATE TABLE IF NOT EXISTS login_failures (
 ip TEXT PRIMARY KEY, failures INTEGER NOT NULL, blocked_until TEXT, updated_at TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=FULL")
        con.execute("PRAGMA busy_timeout=30000")
        return con

    def initialize(self) -> None:
        with self.connect() as con:
            con.executescript(SCHEMA)
            # Google Drive support was removed. Preserve old metadata for audit,
            # but stop all unfinished cloud work and normalize local ownership.
            con.execute("UPDATE jobs SET state='Cancelled', error='Google Drive support removed', lease_token=NULL, lease_until=NULL, updated_at=? WHERE kind='upload' AND state NOT IN ('Complete','Cancelled')", (utcnow(),))
            con.execute("UPDATE clips SET state='Archived locally', updated_at=? WHERE state='Uploaded verified'", (utcnow(),))

    @contextlib.contextmanager
    def immediate(self) -> Iterator[sqlite3.Connection]:
        con = self.connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def health(self, component: str, severity: str, message: str) -> None:
        with self.connect() as con:
            con.execute("INSERT INTO health_events(component,severity,message,created_at) VALUES(?,?,?,?)", (component, severity, message, utcnow()))

    def create_clip(self, camera_id: str, path: Path, storage_name: str, started_at: datetime, timezone: str) -> str:
        clip_id = str(uuid.uuid4())
        now = utcnow()
        with self.connect() as con:
            con.execute("""INSERT INTO clips(id,camera_id,state,recording_path,storage_name,started_at,local_timezone,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?,?,?)""", (clip_id, camera_id, "Recording", str(path), storage_name, started_at.isoformat(), timezone, now, now))
        return clip_id

    def complete_clip(self, clip_id: str, end: datetime, size: int, state: str = "Finalized") -> None:
        with self.connect() as con:
            con.execute("UPDATE clips SET state=?, ended_at=?, size_bytes=?, updated_at=? WHERE id=? AND state IN ('Recording','Finalizing')", (state, end.isoformat(), size, utcnow(), clip_id))

    def queue_archive(self, clip_id: str) -> str:
        job_id = str(uuid.uuid4())
        with self.immediate() as con:
            row = con.execute("SELECT state,recording_path FROM clips WHERE id=?", (clip_id,)).fetchone()
            if not row or row["state"] not in {"Finalized", "Interrupted"} or not row["recording_path"]:
                raise ValueError("clip is not eligible for archive")
            con.execute("UPDATE clips SET state='Archive queued', protected=1, updated_at=? WHERE id=?", (utcnow(), clip_id))
            con.execute("INSERT INTO jobs(id,kind,clip_id,state,created_at,updated_at) VALUES(?,?,?,?,?,?)", (job_id, "archive", clip_id, "Queued", utcnow(), utcnow()))
        return job_id

    def queue_delete_archive(self, clip_id: str) -> str:
        job_id = str(uuid.uuid4())
        with self.immediate() as con:
            row = con.execute("SELECT state,archive_path FROM clips WHERE id=?", (clip_id,)).fetchone()
            if not row or not row["archive_path"] or row["state"] == "Deleted":
                raise ValueError("archive is not available")
            con.execute("UPDATE clips SET state='Delete pending', updated_at=? WHERE id=?", (utcnow(), clip_id))
            con.execute("INSERT INTO jobs(id,kind,clip_id,state,created_at,updated_at) VALUES(?,?,?,?,?,?)", (job_id, "delete_archive", clip_id, "Queued", utcnow(), utcnow()))
        return job_id

    def claim(self, kind: str, worker: str, lease_seconds: int = 120) -> sqlite3.Row | None:
        token = f"{worker}:{uuid.uuid4()}"
        now = datetime.now(UTC)
        until = (now + timedelta(seconds=lease_seconds)).isoformat()
        with self.immediate() as con:
            row = con.execute("""SELECT * FROM jobs WHERE kind=? AND state NOT IN ('Complete','Cancelled','Failed')
                AND (not_before IS NULL OR not_before<=?) AND (lease_until IS NULL OR lease_until<?)
                ORDER BY created_at LIMIT 1""", (kind, now.isoformat(), now.isoformat())).fetchone()
            if not row:
                return None
            changed = con.execute("UPDATE jobs SET state='Claimed', lease_token=?, lease_until=?, updated_at=? WHERE id=? AND state=?", (token, until, utcnow(), row["id"], row["state"])).rowcount
            if not changed:
                return None
            return con.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone()

    def transition_job(self, job_id: str, token: str, state: str, *, payload: dict[str, Any] | None = None, error: str | None = None, retry_seconds: int | None = None) -> bool:
        sets = ["state=?", "updated_at=?"]
        values: list[Any] = [state, utcnow()]
        if payload is not None:
            sets.append("payload=?"); values.append(json.dumps(payload))
        if error is not None:
            sets.append("error=?"); values.append(error)
        if retry_seconds is not None:
            sets.extend(["attempt=attempt+1", "not_before=?", "lease_token=NULL", "lease_until=NULL"])
            values.append((datetime.now(UTC) + timedelta(seconds=retry_seconds)).isoformat())
        elif state in {"Complete", "Cancelled", "Failed"}:
            sets.extend(["lease_token=NULL", "lease_until=NULL"])
        else:
            sets.append("lease_until=?")
            values.append((datetime.now(UTC) + timedelta(minutes=10)).isoformat())
        values.extend([job_id, token])
        with self.connect() as con:
            return bool(con.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id=? AND lease_token=? AND state<>'Cancelled'", values).rowcount)

    def owns_job(self, job_id: str, token: str) -> bool:
        with self.connect() as con:
            return con.execute("SELECT 1 FROM jobs WHERE id=? AND lease_token=? AND state<>'Cancelled'", (job_id, token)).fetchone() is not None

    def get_clip(self, clip_id: str) -> sqlite3.Row | None:
        with self.connect() as con:
            return con.execute("SELECT * FROM clips WHERE id=?", (clip_id,)).fetchone()

    def list_clips(self, limit: int = 100, offset: int = 0) -> list[sqlite3.Row]:
        with self.connect() as con:
            return con.execute("SELECT * FROM clips ORDER BY started_at DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()

    def eligible_for_retention(self, before: datetime) -> list[sqlite3.Row]:
        with self.connect() as con:
            return con.execute("""SELECT * FROM clips WHERE state IN ('Finalized','Interrupted','Delete pending') AND protected=0
                AND recording_path IS NOT NULL AND ended_at<?""", (before.isoformat(),)).fetchall()
