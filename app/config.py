from __future__ import annotations

import copy
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class MountConfig:
    name: str
    path: Path
    fs_uuid: str
    source: str | None
    reserve_bytes: int


@dataclass(frozen=True)
class CameraConfig:
    id: str
    name: str
    enabled: bool
    main_url: str
    sub_url: str | None
    transport: str
    timeout_us: int
    reconnect_initial: int
    reconnect_max: int


@dataclass(frozen=True)
class Settings:
    path: Path
    data_dir: Path
    timezone: ZoneInfo
    ffmpeg: str
    ffprobe: str
    retention_hours: int
    expected_segment_bytes: int
    recording_a: MountConfig
    recording_b: MountConfig
    archive: MountConfig
    cameras: tuple[CameraConfig, ...]
    dashboard: dict[str, Any]
    preview: dict[str, Any]
    google: dict[str, Any]


def _positive(value: Any, name: str, minimum: int = 1) -> int:
    if not isinstance(value, int) or value < minimum:
        raise ConfigError(f"{name} must be an integer >= {minimum}")
    return value


def _mount(name: str, raw: dict[str, Any]) -> MountConfig:
    try:
        raw_path = str(raw["path"])
        path = Path(raw_path)
        uuid = str(raw["fs_uuid"]).strip()
    except KeyError as exc:
        raise ConfigError(f"storage.{name}.{exc.args[0]} is required") from exc
    # The appliance runs on Debian; allow POSIX paths to be syntax-validated
    # during cross-platform development as well.
    if not (path.is_absolute() or raw_path.startswith("/")) or not uuid:
        raise ConfigError(f"storage.{name} needs an absolute path and fs_uuid")
    return MountConfig(name, path, uuid, raw.get("source"), _positive(raw.get("reserve_gib", 15), f"storage.{name}.reserve_gib", 0) * 1024**3)


def _camera(raw: dict[str, Any]) -> CameraConfig:
    required = ("id", "name", "main_url")
    missing = [key for key in required if not raw.get(key)]
    if missing:
        raise ConfigError(f"camera missing: {', '.join(missing)}")
    if not str(raw["main_url"]).startswith("rtsp://"):
        raise ConfigError(f"camera {raw['id']}: main_url must be rtsp://")
    return CameraConfig(
        id=str(raw["id"]), name=str(raw["name"]), enabled=bool(raw.get("enabled", True)),
        main_url=str(raw["main_url"]), sub_url=raw.get("sub_url"),
        transport=str(raw.get("transport", "tcp")), timeout_us=_positive(raw.get("timeout_us", 15_000_000), "camera.timeout_us"),
        reconnect_initial=_positive(raw.get("reconnect_initial_seconds", 2), "camera.reconnect_initial_seconds"),
        reconnect_max=_positive(raw.get("reconnect_max_seconds", 60), "camera.reconnect_max_seconds"),
    )


def load(path: str | Path | None = None) -> Settings:
    config_path = Path(path or os.environ.get("SECURITY_CAMERA_CONFIG", "/etc/security-camera/config.toml"))
    try:
        with config_path.open("rb") as handle:
            raw = tomllib.load(handle)
    except FileNotFoundError as exc:
        raise ConfigError(f"configuration not found: {config_path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML: {exc}") from exc
    general = raw.get("general", {})
    try:
        timezone = ZoneInfo(general.get("timezone", "UTC"))
    except Exception as exc:
        raise ConfigError("general.timezone is invalid") from exc
    storage = raw.get("storage", {})
    cameras = tuple(_camera(item) for item in raw.get("cameras", []))
    ids = [camera.id for camera in cameras]
    if len(ids) != len(set(ids)) or not cameras:
        raise ConfigError("at least one camera with a unique id is required")
    return Settings(
        path=config_path, data_dir=Path(general.get("data_dir", "/var/lib/security-camera")), timezone=timezone,
        ffmpeg=str(general.get("ffmpeg", "/usr/bin/ffmpeg")), ffprobe=str(general.get("ffprobe", "/usr/bin/ffprobe")),
        retention_hours=_positive(general.get("retention_hours", 168), "general.retention_hours"),
        expected_segment_bytes=_positive(general.get("expected_segment_gib", 8), "general.expected_segment_gib", 0) * 1024**3,
        recording_a=_mount("recording_a", storage.get("recording_a", {})),
        recording_b=_mount("recording_b", storage.get("recording_b", {})),
        archive=_mount("archive", storage.get("archive", {})), cameras=cameras,
        dashboard=copy.deepcopy(raw.get("dashboard", {})), preview=copy.deepcopy(raw.get("preview", {})), google=copy.deepcopy(raw.get("google", {})),
    )


def assert_secure_config(path: Path) -> None:
    """Reject world-readable secret configuration on Linux."""
    mode = path.stat().st_mode & 0o777
    if mode & 0o007:
        raise ConfigError(f"{path} must not be readable by other users (expected 0640 or stricter)")
