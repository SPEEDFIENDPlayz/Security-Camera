from __future__ import annotations

import argparse
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

from app.archive.manager import ArchiveWorker
from app.config import assert_secure_config, load
from app.control import ControlWorker
from app.database import Database
from app.logging_setup import configure_logging
from app.recorder.manager import run as run_recorder
from app.storage.reconciliation import reconcile
from app.storage.retention import run_retention
from app.storage.mounts import validate_mount


def _settings(args):
    settings = load(args.config)
    assert_secure_config(settings.path)
    db = Database(settings.data_dir / "security-camera.db"); db.initialize()
    return settings, db


def authorize(settings) -> None:
    client = settings.google.get("oauth_client_secrets")
    token = settings.google.get("token_file")
    if not client or not token: raise SystemExit("google.oauth_client_secrets and token_file are required")
    flow = InstalledAppFlow.from_client_secrets_file(client, ["https://www.googleapis.com/auth/drive.file"])
    credentials = flow.run_local_server(host="127.0.0.1", port=0, open_browser=True, authorization_prompt_message="Open this URL in the local browser: {url}")
    Path(token).write_text(credentials.to_json())
    Path(token).chmod(0o640)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=None)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("recorder"); sub.add_parser("archive"); sub.add_parser("maintenance"); sub.add_parser("control"); sub.add_parser("authorize-drive")
    args = parser.parse_args(); configure_logging()
    settings, db = _settings(args)
    if args.command == "recorder": run_recorder(settings, db)
    elif args.command == "archive": ArchiveWorker(settings, db).loop()
    elif args.command == "maintenance":
        for mount in (settings.recording_a, settings.recording_b, settings.archive):
            status = validate_mount(mount)
            if not (status.available and status.writable and not status.reason):
                db.health("storage", "critical", f"{mount.name} unavailable: {status.reason}")
        reconcile(settings, db); run_retention(settings, db)
    elif args.command == "control": ControlWorker(settings.path, db).loop()
    else: authorize(settings)


if __name__ == "__main__": main()
