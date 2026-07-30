from __future__ import annotations

import signal
import threading
import time

from app.config import Settings, load
from app.database import Database
from app.recorder.worker import RecorderWorker


def run(settings: Settings, db: Database) -> None:
    workers: dict[str, RecorderWorker] = {}
    threads: dict[str, threading.Thread] = {}
    reload_requested = False
    def start(camera):
        worker = RecorderWorker(settings, db, camera)
        workers[camera.id] = worker
        thread = threading.Thread(target=worker.run, name=f"record-{camera.id}", daemon=False)
        threads[camera.id] = thread; thread.start()
    for camera in settings.cameras:
        if camera.enabled: start(camera)
    def stop(*_args):
        for worker in workers.values(): worker.request_stop()
    def request_reload(*_args):
        nonlocal reload_requested
        reload_requested = True
    signal.signal(signal.SIGTERM, stop); signal.signal(signal.SIGINT, stop); signal.signal(signal.SIGHUP, request_reload)
    while threads:
        for key, thread in list(threads.items()):
            if not thread.is_alive():
                thread.join(); threads.pop(key); workers.pop(key, None)
        if reload_requested:
            reload_requested = False
            try:
                updated = load(settings.path)
                old = {camera.id: camera for camera in settings.cameras}
                new = {camera.id: camera for camera in updated.cameras}
                for camera_id, worker in list(workers.items()):
                    replacement = new.get(camera_id)
                    if not replacement or not replacement.enabled or old.get(camera_id) != replacement:
                        worker.request_stop(); threads[camera_id].join(timeout=15)
                        if not threads[camera_id].is_alive(): threads.pop(camera_id); workers.pop(camera_id)
                settings = updated
                for camera in settings.cameras:
                    if camera.enabled and camera.id not in workers: start(camera)
            except Exception as exc:
                db.health("recorder", "error", f"configuration reload rejected: {exc}")
        time.sleep(0.5)
