from datetime import UTC, datetime
from pathlib import Path

from app.database import Database
from app.recorder.worker import next_boundary


def test_calendar_boundary_is_noon_in_configured_zone():
    from zoneinfo import ZoneInfo
    zone = ZoneInfo("America/Los_Angeles")
    result = next_boundary(datetime(2026, 7, 29, 17, 0, tzinfo=UTC), zone)
    assert result.astimezone(zone).hour == 12
    assert result.astimezone(zone).minute == 0


def test_archive_queue_protects_source(tmp_path: Path):
    db = Database(tmp_path / "state.db"); db.initialize()
    clip_id = db.create_clip("door", tmp_path / "clip.mkv", "recording_a", datetime.now(UTC), "UTC")
    db.complete_clip(clip_id, datetime.now(UTC), 12)
    db.queue_archive(clip_id)
    clip = db.get_clip(clip_id)
    assert clip["state"] == "Archive queued"
    assert clip["protected"] == 1


def test_job_can_only_be_claimed_once(tmp_path: Path):
    db = Database(tmp_path / "state.db"); db.initialize()
    clip_id = db.create_clip("door", tmp_path / "clip.mkv", "recording_a", datetime.now(UTC), "UTC")
    db.complete_clip(clip_id, datetime.now(UTC), 12); db.queue_archive(clip_id)
    assert db.claim("archive", "one") is not None
    assert db.claim("archive", "two") is None
