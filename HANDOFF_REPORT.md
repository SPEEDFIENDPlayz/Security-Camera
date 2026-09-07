# Security Camera Appliance — Handoff Report

## Purpose

This repository is a Debian 13/Xfce surveillance appliance for two IP cameras. It records compressed RTSP streams continuously with FFmpeg stream copy, keeps seven days of local recordings, and lets an administrator copy selected finalized clips to a separate archive drive. The design targets an older quad-core Intel system with 4 GB RAM; normal recording must not decode or re-encode video.

This report is intended to let another engineering chat continue the work without reconstructing the prior discussion.

## User-approved operating model

- Debian 13, normally unattended, with recording started at boot by systemd.
- One configured 2 TB USB drive is the sole recording destination. There is no recording spillover drive.
- A different mounted drive is the local archive destination.
- Both drives are selected during first-run setup and must already be mounted by Debian. The application never formats disks, edits `/etc/fstab`, or mounts drives.
- Two RTSP cameras: Main Door Walkway and Outside Garage. The user supplied private camera URLs/credentials in chat; never commit or print those credentials. The wizard must collect them interactively and only store them in protected configuration.
- Main recordings are twelve-hour clips aligned to local midnight/noon boundaries. UTC timestamps are authoritative in SQLite; local timezone/display values are retained.
- Retention defaults to 168 hours and applies only to finalized, non-protected clips still on the recording drive. Archive files are retained indefinitely and are never automatically deleted.
- Google Drive/OAuth/upload functionality was explicitly removed from the current version.
- Dashboard is Flask/Jinja/Gunicorn, local-only by default (`127.0.0.1:8080`); optional LAN binding requires authentication and should never be exposed to the public internet.
- Live preview is disabled by default, separate from recording, sub-stream only, and must use stream-copy remuxing or report unavailable. No silent transcoding.

## Current repository state

- Remote: `https://github.com/SPEEDFIENDPlayz/Security-Camera.git`
- Default branch: `main`
- Latest pushed commit: `c822b5d` (`Merge graphical setup wizard`), containing wizard commit `dfd7834`.
- Working tree was clean when this report was created.
- Important entry points:
  - `setup.sh`: root bootstrap/installer and first-run launcher.
  - `app/setup/wizard.py`: Tkinter multi-page setup/reconfigure wizard.
  - `app/main.py`: CLI entry point (`security-camera setup`, recorder, archive, maintenance, dashboard modes).
  - `app/config.py`: TOML configuration models and loading/validation.
  - `app/database.py`: SQLite schema/migrations and metadata state.
  - `app/recorder/`: FFmpeg command construction, camera workers, recording manager.
  - `app/archive/manager.py`: local archive copy/verification lifecycle; no cloud upload.
  - `app/storage/mounts.py`, `retention.py`, `reconciliation.py`: drive validation and maintenance.
  - `app/dashboard/`: Flask server, routes, templates, login, clips/settings/status pages.
  - `app/live_view/manager.py`: isolated optional preview relay.
  - `systemd/`: recorder, archive, dashboard, maintenance service/timer units.
  - `config/config.example.toml`: sanitized example only; real config belongs at `/etc/security-camera/config.toml`.
  - `tests/`: current unit tests (`test_core.py`, `test_setup.py`).

## Setup wizard behavior

`setup.sh` installs Debian prerequisites (`ffmpeg`, Python 3, venv tooling, Tkinter, PolicyKit), creates the `securitycam` service account, installs the project under `/opt/security-camera`, installs the systemd units, disables services until setup is complete, and launches `security-camera setup`.

The wizard pages are:

1. Welcome and existing-configuration detection.
2. Recording-drive selection.
3. Archive-drive selection.
4. Camera 1 details and FFprobe test.
5. Camera 2 details and FFprobe test.
6. Dashboard password and optional LAN binding/TLS settings.
7. Review/apply/completion.

It discovers currently mounted non-root filesystems with `lsblk`, rejects read-only and duplicate drives, records mount point/UUID/source, constructs encoded RTSP URLs, and requires a successful FFprobe test for each enabled camera before Finish. Blank password fields preserve existing secrets on reconfigure. Protected TOML writing uses temporary file, fsync, atomic replacement, root ownership, and mode 0640. Services are enabled/started only after a successful apply.

## Safety and ownership model

- Recorder service owns FFmpeg processes, in-progress files, finalization, and camera health.
- Archive service owns archive copies, temporary/quarantine files, archive verification, and source deletion after successful local publication.
- Maintenance service/timer owns retention, mount/storage health, reconciliation, and stale temporary cleanup.
- Dashboard only reads SQLite and enqueues validated requests; it must never directly move/delete video files.
- SQLite is the metadata source of truth. State changes should be transactional, use conditional updates/job claims, and preserve clips when state is uncertain after a crash.
- Before every write, validate that the configured mount is active, matches UUID/source/device identity, is writable, has reserve space, and is not an unmounted directory on the OS/root filesystem.

## Archive lifecycle

Archive action: mark source protected and queue job; validate archive mount/capacity; copy to a temporary archive filename; flush/fsync/close; compare size; ffprobe; optionally checksum; atomically rename and fsync directory; transactionally mark local archive authoritative; delete source only after verification; update SQLite. On failure preserve source, quarantine/remove incomplete destination, record error, and allow safe retry. Archive files stay forever unless manually deleted through an authenticated, confirmed dashboard action with canonical-path containment checks.

## Recovery requirements

On restart/power loss, reconcile recorded in-progress entries and filesystem facts. Recover valid partial MKVs with ffprobe or mark them Interrupted; never delete uncertain footage. Reconcile archive temp files, published copies, source deletion pending states, and database/filesystem mismatches conservatively. Legacy upload jobs should be cancelled and legacy uploaded clips normalized to locally archived state without using cloud IDs.

## Known verification status and next work

Static checks previously performed: Python compilation, config parsing, wizard URL construction, CLI help. Real Debian integration has not been completed in this Windows development environment. The next chat should test on Debian with mounted drives and reachable cameras, then fix any runtime issues.

Priority checks:

1. Run the wizard from an Xfce desktop and confirm Tkinter appears after PolicyKit authorization.
2. Verify FFmpeg recording uses `-rtsp_transport tcp`, `-c:v copy`, `-c:a copy`, MKV output, aligned boundaries, reconnect behavior, and low CPU.
3. Verify systemd boot ordering, permissions, journal logs, and no writes when either mount is missing/read-only/wrong UUID.
4. Exercise archive copy success/failure, source preservation, retention exclusions, manual deletion, and reconciliation after interruption.
5. Exercise dashboard authentication, CSRF, LAN binding, pagination, idle resource use, and preview start/stop/timeout.
6. Expand tests for mount filtering, state transitions, leases/races, DST boundaries, atomic config rollback, and recovery.

## Operational commands (Debian)

```sh
chmod +x setup.sh
./setup.sh
sudo systemctl status security-camera-recorder.service
sudo journalctl -u security-camera-recorder.service -f
sudo bash scripts/health_check.sh
```

Dashboard after setup: `http://127.0.0.1:8080`.

## Security reminders

Do not place real camera passwords, dashboard passwords, OAuth tokens, or authenticated RTSP URLs in Git, issues, logs, screenshots, or browser responses. Keep `/etc/security-camera/config.toml` root-owned and mode 0640. Treat all camera credentials in the prior conversation as sensitive input.
