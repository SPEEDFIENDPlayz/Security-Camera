from __future__ import annotations

import subprocess
from pathlib import Path

from app.config import CameraConfig, Settings


def command(settings: Settings, camera: CameraConfig, output: Path, duration_seconds: float) -> list[str]:
    """Build a direct-copy command. Do not log this list without redaction."""
    return [
        settings.ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "warning",
        "-rtsp_transport", camera.transport, "-rw_timeout", str(camera.timeout_us),
        "-i", camera.main_url, "-map", "0:v:0?", "-map", "0:a?", "-c", "copy",
        "-t", str(max(1, int(duration_seconds))), "-f", "matroska", str(output),
    ]


def probe(settings: Settings, path: Path) -> bool:
    try:
        result = subprocess.run(
            [settings.ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=nk=1:nw=1", str(path)],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=60, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and bool(result.stdout.strip())
