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


def _mountinfo(path: Path) -> tuple[str, str] | None:
    target = str(path.resolve())
    best: tuple[int, str, str] | None = None
    try:
        lines = Path("/proc/self/mountinfo").read_text().splitlines()
    except FileNotFoundError:
        return None
    for line in lines:
        left, right = line.split(" - ", 1)
        fields, post = left.split(), right.split()
        mount_point = _unescape_mountinfo(fields[4])
        if target == mount_point or target.startswith(mount_point.rstrip("/") + "/"):
            candidate = (len(mount_point), post[1], post[0])
            if best is None or candidate[0] > best[0]:
                best = candidate
    return (best[1], best[2]) if best else None


def validate_mount(config: MountConfig, required_bytes: int = 0) -> MountStatus:
    path = config.path
    if not path.exists() or not path.is_dir():
        return MountStatus(config, "", "", False, False, 0, 0, "mount point missing")
    info = _mountinfo(path)
    if not info:
        return MountStatus(config, "", "", False, False, 0, 0, "not an active mount")
    source, fs_type = info
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
    if not writable:
        return MountStatus(config, source, fs_type, True, False, free, total, "mount is read-only")
    if free < required_bytes + config.reserve_bytes:
        return MountStatus(config, source, fs_type, True, True, free, total, "insufficient free space including reserve")
    return MountStatus(config, source, fs_type, True, True, free, total)


def safe_child(root: Path, child: Path) -> Path:
    resolved_root, resolved_child = root.resolve(), child.resolve()
    if resolved_root != resolved_child and resolved_root not in resolved_child.parents:
        raise MountError("path escapes configured storage root")
    return resolved_child
