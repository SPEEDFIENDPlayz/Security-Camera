from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from app.config import CameraConfig, Settings


@dataclass
class Preview:
    camera_id: str
    directory: Path
    process: subprocess.Popen
    expires_at: float


class PreviewManager:
    """Starts HLS *remux* relays only after an explicit dashboard request."""
    def __init__(self, settings: Settings):
        self.settings = settings
        self.root = settings.data_dir / "previews"
        self.active: dict[str, Preview] = {}

    def reap(self) -> None:
        now = time.monotonic()
        for camera_id, preview in list(self.active.items()):
            if now >= preview.expires_at or preview.process.poll() is not None:
                self.stop(camera_id)

    def start(self, camera: CameraConfig, requested_seconds: int | None = None) -> Preview:
        self.reap()
        if not self.settings.preview.get("enabled", True): raise RuntimeError("live preview is disabled")
        if not camera.sub_url: raise RuntimeError("no sub-stream is configured for this camera")
        if camera.id in self.active: return self.active[camera.id]
        if len(self.active) >= int(self.settings.preview.get("max_concurrent", 1)):
            raise RuntimeError("maximum concurrent previews reached")
        default = int(self.settings.preview.get("default_timeout_seconds", 300))
        maximum = int(self.settings.preview.get("maximum_timeout_seconds", 3600))
        seconds = min(maximum, max(1, requested_seconds or default))
        directory = self.root / camera.id
        shutil.rmtree(directory, ignore_errors=True); directory.mkdir(parents=True, exist_ok=True)
        output = directory / "index.m3u8"
        args = [self.settings.ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "warning", "-rtsp_transport", camera.transport, "-rw_timeout", str(camera.timeout_us), "-i", camera.sub_url, "-map", "0:v:0?", "-map", "0:a?", "-c", "copy", "-f", "hls", "-hls_time", "2", "-hls_list_size", "6", "-hls_flags", "delete_segments+append_list", str(output)]
        process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        preview = Preview(camera.id, directory, process, time.monotonic() + seconds)
        self.active[camera.id] = preview
        return preview

    def stop(self, camera_id: str) -> None:
        preview = self.active.pop(camera_id, None)
        if not preview: return
        if preview.process.poll() is None:
            preview.process.terminate()
            try: preview.process.wait(timeout=5)
            except subprocess.TimeoutExpired: preview.process.kill()
        shutil.rmtree(preview.directory, ignore_errors=True)
