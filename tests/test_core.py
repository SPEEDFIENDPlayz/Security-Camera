from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from argon2 import PasswordHasher

from app.config import CameraConfig, ConfigError, MountConfig, Settings, load
from app.dashboard.server import create_app
from app.database import Database
from app.recorder.worker import next_boundary
from app.storage.mounts import MountStatus
from app.storage.retention import run_retention


def test_calendar_boundary_is_noon_in_configured_zone():
    from zoneinfo import ZoneInfo
    zone = ZoneInfo("America/Los_Angeles")
    result = next_boundary(datetime(2026, 7, 29, 17, 0, tzinfo=UTC), zone)
    assert result.astimezone(zone).hour == 12
    assert result.astimezone(zone).minute == 0


def test_archive_queue_protects_source(tmp_path: Path):
    db = Database(tmp_path / "state.db"); db.initialize()
    clip_id = db.create_clip("door", tmp_path / "clip.mkv", "recording", datetime.now(UTC), "UTC")
    db.complete_clip(clip_id, datetime.now(UTC), 12)
    db.queue_archive(clip_id)
    clip = db.get_clip(clip_id)
    assert clip["state"] == "Archive queued"
    assert clip["protected"] == 1


def test_job_can_only_be_claimed_once(tmp_path: Path):
    db = Database(tmp_path / "state.db"); db.initialize()
    clip_id = db.create_clip("door", tmp_path / "clip.mkv", "recording", datetime.now(UTC), "UTC")
    db.complete_clip(clip_id, datetime.now(UTC), 12); db.queue_archive(clip_id)
    assert db.claim("archive", "one") is not None
    assert db.claim("archive", "two") is None


def test_expired_in_progress_job_can_be_reclaimed(tmp_path: Path):
    db = Database(tmp_path / "state.db"); db.initialize()
    clip_id = db.create_clip("door", tmp_path / "clip.mkv", "recording", datetime.now(UTC), "UTC")
    db.complete_clip(clip_id, datetime.now(UTC), 12); db.queue_archive(clip_id)
    first = db.claim("archive", "one")
    assert first is not None
    assert db.transition_job(first["id"], first["lease_token"], "Copying")
    with db.connect() as con:
        con.execute("UPDATE jobs SET lease_until=? WHERE id=?", ((datetime.now(UTC) - timedelta(minutes=1)).isoformat(), first["id"]))
    second = db.claim("archive", "two")
    assert second is not None
    assert second["id"] == first["id"]


def test_retention_does_not_touch_files_when_recording_mount_is_unavailable(tmp_path: Path, monkeypatch):
    recording = tmp_path / "recording"
    recording.mkdir()
    clip_path = recording / "old.mkv"
    clip_path.write_bytes(b"footage")
    db = Database(tmp_path / "state.db"); db.initialize()
    clip_id = db.create_clip("door", clip_path, "recording", datetime.now(UTC) - timedelta(days=8), "UTC")
    db.complete_clip(clip_id, datetime.now(UTC) - timedelta(days=8), clip_path.stat().st_size)
    settings = SimpleNamespace(
        recording=MountConfig("recording", recording, "recording-uuid", None, 0),
        retention_hours=168,
    )
    monkeypatch.setattr("app.storage.retention.validate_mount", lambda *args, **kwargs: MountStatus(settings.recording, "", "", False, False, 0, 0, "not an active mount"))
    assert run_retention(settings, db) == 0
    assert clip_path.exists()


def test_config_rejects_same_storage_filesystem(tmp_path: Path):
    config = tmp_path / "config.toml"
    config.write_text("""
[general]
data_dir = "/var/lib/security-camera"
[storage.recording]
path = "/srv/recording"
fs_uuid = "same"
[storage.archive]
path = "/srv/archive"
fs_uuid = "same"
[[cameras]]
id = "door"
name = "Door"
main_url = "rtsp://camera.example/main"
""", encoding="utf-8")
    try:
        load(config)
    except ConfigError as exc:
        assert "distinct filesystems" in str(exc)
    else:
        raise AssertionError("same storage filesystem was accepted")


def test_login_rejects_external_next_redirect(tmp_path: Path):
    recording = MountConfig("recording", tmp_path / "recording", "recording-uuid", None, 0)
    archive = MountConfig("archive", tmp_path / "archive", "archive-uuid", None, 0)
    settings = Settings(
        path=tmp_path / "config.toml", data_dir=tmp_path, timezone=ZoneInfo("UTC"),
        ffmpeg="ffmpeg", ffprobe="ffprobe", retention_hours=168, expected_segment_bytes=1,
        recording=recording, archive=archive,
        cameras=(CameraConfig("door", "Door", True, "rtsp://camera.example/main", None, "tcp", 1, 1, 1),),
        dashboard={"lan_enabled": True, "session_secret": "test-secret", "admin_password_hash": PasswordHasher().hash("password123")},
        preview={"enabled": False},
    )
    db = Database(tmp_path / "state.db"); db.initialize()
    app = create_app(settings, db)
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    response = app.test_client().post("/login?next=https://attacker.example", data={"password": "password123"})
    assert response.status_code == 302
    assert response.headers["Location"] == "/"
