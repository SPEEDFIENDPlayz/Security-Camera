from __future__ import annotations

import argparse

from app.archive.manager import ArchiveWorker
from app.config import assert_secure_config, load
from app.database import Database
from app.logging_setup import configure_logging
from app.recorder.manager import run as run_recorder
from app.storage.reconciliation import reconcile
from app.storage.retention import run_retention
from app.storage.mounts import validate_mount
from app.setup.wizard import run_setup


def _settings(args):
    settings = load(args.config)
    assert_secure_config(settings.path)
    db = Database(settings.data_dir / "security-camera.db"); db.initialize()
    return settings, db


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=None)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("recorder"); sub.add_parser("archive"); sub.add_parser("maintenance"); sub.add_parser("setup")
    args = parser.parse_args(); configure_logging()
    if args.command == "setup":
        run_setup()
        return
    settings, db = _settings(args)
    if args.command == "recorder": run_recorder(settings, db)
    elif args.command == "archive": ArchiveWorker(settings, db).loop()
    elif args.command == "maintenance":
        for mount in (settings.recording, settings.archive):
            status = validate_mount(mount)
            if not (status.available and status.writable and not status.reason):
                db.health("storage", "critical", f"{mount.name} unavailable: {status.reason}")
        reconcile(settings, db); run_retention(settings, db)


if __name__ == "__main__": main()
