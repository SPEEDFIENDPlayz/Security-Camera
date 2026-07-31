# Security Camera Appliance

This Debian 13 project records RTSP cameras with FFmpeg direct stream copy, maintains aligned local-time MKV clips, archives selected clips to a third verified disk, and uploads the verified archive copy to Google Drive. It is designed to run under systemd without Xfce or any desktop session.

## Safety model

- One configured 2 TB USB disk is the sole recording target. If it is unavailable, read-only, or below its reserve, new segments pause and retry; there is no recording spillover disk.
- A separate configured disk is the archive target. Both disks are selected at boot by their configured mount paths and filesystem UUIDs in TOML, and every write revalidates the active mount identity.
- The archive drive is a separate, indefinitely retained local copy. An archive job protects its original source until a copied, fsynced, size-checked, `ffprobe`-validated archive file has been atomically published.
- The application verifies every configured mount against its active mount and expected UUID before writing. An unmounted directory is never treated as storage.
- The archive worker is the only process that copies/removes archive files. The maintenance worker is the only process that executes retention deletion. The dashboard only queues jobs.
- FFmpeg records with `-c copy`. Preview starts only after a Show Feed click and uses a sub-stream HLS remux (`-c copy`); recording never decodes or transcodes frames.

## Install

1. Install Debian, mount all three disks at their permanent paths, and identify each filesystem UUID with `blkid`.
2. Copy this repository to the Debian host. Run `sudo ./scripts/install.sh` from its root.
3. Edit `/etc/security-camera/config.toml`. Replace every UUID, source, path, camera URL, secret, and `session_secret`. Do not place real configuration in Git.
4. Grant `securitycam` group access to each mounted recording/archive directory, without making the disks world-writable.
5. Run `sudo systemctl restart security-camera-recorder security-camera-archive security-camera-dashboard security-camera-control`.
6. Inspect `sudo ./scripts/health_check.sh` and `journalctl -u security-camera-recorder -f`.

The supplied unit files use `/srv/security/...`; mount the 2 TB recorder at `/srv/security/recording` and the archive disk at `/srv/security/archive`, or update the TOML and unit `ReadWritePaths` together. The application validates both mounts during startup and maintenance and will never write through an unmounted mount-point directory.

## Google Drive

Create a Google Cloud **Desktop** OAuth client, enable Drive API, copy its client JSON to `/etc/security-camera/google-client.json` with `root:securitycam` ownership and `0640` permissions, configure a target folder ID, then run:

```sh
sudo -u securitycam SECURITY_CAMERA_CONFIG=/etc/security-camera/config.toml /opt/security-camera/.venv/bin/security-camera authorize-drive
```

Complete the local browser consent screen. The resulting protected token file enables resumable uploads. The upload worker uses `drive.file` and a stable clip UUID app property to avoid duplicates.

## Dashboard and LAN safety

The default dashboard listens on `127.0.0.1:8080`. Set a non-empty Argon2id `admin_password_hash` even in local-only mode: it is required to delete an archive or replace configuration. To enable LAN access, also set `lan_enabled = true`, TLS certificate/key paths, and a specific LAN address where possible. Restrict the port to the trusted subnet with nftables/ufw; never port-forward it to the public Internet.

Generate a password hash with:

```sh
/opt/security-camera/.venv/bin/python -c "from argon2 import PasswordHasher; print(PasswordHasher().hash(input('Password: ')))"
```

## Operations

- Archive eligible clips in the dashboard. Do not remove original files manually while an archive job is active or failed.
- Archive deletion requires the dashboard administrator password and typing `DELETE`; it cancels a live upload before deletion. Cloud metadata is retained in SQLite.
- Retention runs hourly and removes only unprotected finalized/interrupt clips older than 168 hours. It never removes archive-drive clips.
- Run `security-camera maintenance` manually after an unclean shutdown to reconcile in-progress recordings; the timer also does this hourly.

## Tests

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
pytest -q
```
