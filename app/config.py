from __future__ import annotations

import copy
import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
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
    recording: MountConfig
    archive: MountConfig
    cameras: tuple[CameraConfig, ...]
    dashboard: dict[str, Any]
    preview: dict[str, Any]


def _positive(value: Any, name: str, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
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
    camera_id = str(raw["id"]).strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", camera_id):
        raise ConfigError(f"camera {camera_id!r}: id contains unsafe characters")
    main_url = str(raw["main_url"])
    parsed = urlsplit(main_url)
    if parsed.scheme.lower() != "rtsp" or not parsed.netloc:
        raise ConfigError(f"camera {raw['id']}: main_url must be rtsp://")
    sub_url = raw.get("sub_url")
    if sub_url:
        sub_parsed = urlsplit(str(sub_url))
        if sub_parsed.scheme.lower() != "rtsp" or not sub_parsed.netloc:
            raise ConfigError(f"camera {raw['id']}: sub_url must be rtsp://")
    transport = str(raw.get("transport", "tcp")).lower()
    if transport not in {"tcp", "udp"}:
        raise ConfigError(f"camera {raw['id']}: transport must be tcp or udp")
    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ConfigError(f"camera {raw['id']}: enabled must be a boolean")
    return CameraConfig(
        id=camera_id, name=str(raw["name"]), enabled=enabled,
        main_url=main_url, sub_url=str(sub_url) if sub_url else None,
        transport=transport, timeout_us=_positive(raw.get("timeout_us", 15_000_000), "camera.timeout_us"),
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
    recording = _mount("recording", storage.get("recording", {}))
    archive = _mount("archive", storage.get("archive", {}))
    if recording.fs_uuid == archive.fs_uuid or recording.path.resolve() == archive.path.resolve():
        raise ConfigError("recording and archive must use two distinct filesystems")
    dashboard = copy.deepcopy(raw.get("dashboard", {}))
    bind = str(dashboard.get("bind", "127.0.0.1"))
    lan_enabled = dashboard.get("lan_enabled", False)
    if not isinstance(lan_enabled, bool):
        raise ConfigError("dashboard.lan_enabled must be a boolean")
    if not lan_enabled and bind not in {"127.0.0.1", "::1", "localhost"}:
        raise ConfigError("dashboard.bind must be local-only unless dashboard.lan_enabled is true")
    if lan_enabled and (not dashboard.get("admin_password_hash") or not dashboard.get("tls_cert") or not dashboard.get("tls_key")):
        raise ConfigError("LAN dashboard mode requires a password hash and TLS certificate/key paths")
    port = _positive(dashboard.get("port", 8080), "dashboard.port")
    if port > 65535:
        raise ConfigError("dashboard.port must be between 1 and 65535")
    dashboard["bind"] = bind
    dashboard["port"] = port
    return Settings(
        path=config_path, data_dir=Path(general.get("data_dir", "/var/lib/security-camera")), timezone=timezone,
        ffmpeg=str(general.get("ffmpeg", "/usr/bin/ffmpeg")), ffprobe=str(general.get("ffprobe", "/usr/bin/ffprobe")),
        retention_hours=_positive(general.get("retention_hours", 168), "general.retention_hours"),
        expected_segment_bytes=_positive(general.get("expected_segment_gib", 8), "general.expected_segment_gib", 0) * 1024**3,
        recording=recording, archive=archive, cameras=cameras,
        dashboard=dashboard, preview=copy.deepcopy(raw.get("preview", {})),
    )


def assert_secure_config(path: Path) -> None:
    """Reject world-readable secret configuration on Linux."""
    mode = path.stat().st_mode & 0o777
    if mode & 0o007:
        raise ConfigError(f"{path} must not be readable by other users (expected 0640 or stricter)")
