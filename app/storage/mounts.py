from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from app.config import MountConfig


class MountError(RuntimeError):
    pass


@dataclass(frozen=True)
class MountStatus:
    config: MountConfig
    source: str
    fs_type: str
    available: bool
    writable: bool
    free_bytes: int
    total_bytes: int
    reason: str = ""


def _unescape_mountinfo(value: str) -> str:
    return value.replace("\\040", " ").replace("\\011", "\t").replace("\\134", "\\")


def _mountinfo(path: Path) -> tuple[str, str, bool] | None:
    target = str(path.resolve())
    best: tuple[int, str, str, bool] | None = None
    try:
        lines = Path("/proc/self/mountinfo").read_text().splitlines()
    except FileNotFoundError:
        return None
    for line in lines:
        left, right = line.split(" - ", 1)
        fields, post = left.split(), right.split()
        mount_point = _unescape_mountinfo(fields[4])
        if target == mount_point or target.startswith(mount_point.rstrip("/") + "/"):
            mount_options = set(fields[5].split(","))
            super_options = set(post[2].split(",")) if len(post) > 2 else set()
            candidate = (len(mount_point), post[1], post[0], "ro" in mount_options or "ro" in super_options)
            if best is None or candidate[0] > best[0]:
                best = candidate
    return (best[1], best[2], best[3]) if best else None


def validate_mount(
    config: MountConfig,
    required_bytes: int = 0,
    *,
    require_writable: bool = True,
    enforce_reserve: bool = True,
) -> MountStatus:
    path = config.path
    if not path.exists() or not path.is_dir():
        return MountStatus(config, "", "", False, False, 0, 0, "mount point missing")
    info = _mountinfo(path)
    if not info:
        return MountStatus(config, "", "", False, False, 0, 0, "not an active mount")
    source, fs_type, mounted_read_only = info
    root_info = _mountinfo(Path("/"))
    if path.resolve() == Path("/") or (root_info and source == root_info[0]):
        return MountStatus(config, source, fs_type, False, False, 0, 0, "refusing root filesystem")
    uuid_link = Path("/dev/disk/by-uuid") / config.fs_uuid
    if not uuid_link.exists():
        return MountStatus(config, source, fs_type, False, False, 0, 0, "configured filesystem UUID not present")
    expected = os.path.realpath(uuid_link)
    actual = os.path.realpath(source) if source.startswith("/") else source
    if actual != expected:
        return MountStatus(config, source, fs_type, False, False, 0, 0, "mounted device does not match configured UUID")
    if config.source and os.path.realpath(config.source) != actual:
        return MountStatus(config, source, fs_type, False, False, 0, 0, "mounted source does not match configuration")
    usage = os.statvfs(path)
    free, total = usage.f_bavail * usage.f_frsize, usage.f_blocks * usage.f_frsize
    writable = os.access(path, os.W_OK)
    if mounted_read_only or not writable:
        if not require_writable:
            return MountStatus(config, source, fs_type, True, False, free, total)
        return MountStatus(config, source, fs_type, True, False, free, total, "mount is read-only")
    if enforce_reserve and free < required_bytes + config.reserve_bytes:
        return MountStatus(config, source, fs_type, True, True, free, total, "insufficient free space including reserve")
    return MountStatus(config, source, fs_type, True, True, free, total)


def safe_child(root: Path, child: Path) -> Path:
    resolved_root, resolved_child = root.resolve(), child.resolve()
    if resolved_root != resolved_child and resolved_root not in resolved_child.parents:
        raise MountError("path escapes configured storage root")
    return resolved_child
