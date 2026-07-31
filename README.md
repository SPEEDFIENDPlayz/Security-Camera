# Security Camera Appliance

This Debian 13 appliance records two RTSP cameras directly to one configured recording drive and copies selected clips to a separate local archive drive. It uses FFmpeg stream copy, never re-encodes normal recordings, and runs unattended through systemd.

## First-time setup

1. Mount the 2 TB recording drive and the separate archive drive in Debian. The wizard does not format drives, create mounts, or modify `/etc/fstab`.
2. Download and unzip this repository on the Debian Xfce desktop.
3. Double-click `setup.sh` and choose **Run**, then approve the administrator password prompt. If the file manager asks, choose **Run in Terminal**; the graphical wizard opens automatically.
4. Select the two already-mounted drives, enter each camera’s RTSP details, test both streams, set the dashboard password, and finish setup.

The wizard records drive mount paths, UUIDs, and device sources in protected `/etc/security-camera/config.toml`. It verifies the mount identity before every recording or archive write, preventing writes into an unmounted directory on the Debian root filesystem.

Re-run `setup.sh` whenever you need to reconfigure cameras, drives, or dashboard access. Existing passwords remain masked; leave a password blank to keep it unchanged.

## Dashboard

After setup, open `http://127.0.0.1:8080` on the Debian computer. It shows storage/camera health, lets you archive finalized clips to the separate archive drive, and provides optional low-resolution on-demand previews.

The default dashboard is local-only. LAN mode requires a dashboard password and TLS certificate/key paths supplied in the wizard. Never expose the dashboard directly to the public internet.

## Safety model

- The single recording drive is the only normal recording destination. If it is missing, read-only, or below its reserve, new recording segments pause and retry.
- Archived clips are copied, fsynced, size-checked, ffprobe-validated, atomically published on the archive disk, then removed from recording storage. Archive-drive clips are retained until you delete them manually.
- Seven-day retention applies only to eligible finalized clips on the recording disk. It never deletes archived clips.
- Google Drive upload is intentionally not part of this version.

## Health checks

```sh
sudo bash scripts/health_check.sh
sudo journalctl -u security-camera-recorder.service -f
```
