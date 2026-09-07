# Continuation Prompt — Security Camera Appliance

You are taking over an existing Debian surveillance-appliance project. Start by reading `HANDOFF_REPORT.md` in the repository root; it is the authoritative context summary. Then inspect `README.md`, `setup.sh`, `app/setup/wizard.py`, `app/config.py`, `app/database.py`, the recorder/archive/storage/dashboard modules, systemd units, and tests before changing anything.

Repository: `https://github.com/SPEEDFIENDPlayz/Security-Camera.git`, default branch `main`. The project is intended for Debian 13/Xfce on legacy hardware. It records two RTSP cameras with FFmpeg direct stream copy, using one selected 2 TB recording drive and one distinct selected archive drive. The setup is GUI-first through `setup.sh` and Tkinter; drives are already mounted by Debian; no formatting or `/etc/fstab` changes are allowed. Google Drive/OAuth has been removed.

Continue from the current code rather than redesigning from scratch. Preserve these invariants:

- Never decode or transcode normal recordings; use argument-array FFmpeg commands and redact credentials.
- Never write below an unmounted mount-point directory. Validate mount identity (UUID/source/device), writability, root-filesystem exclusion, and reserve space before writes.
- Recording is single-drive only, with safe pause/retry when unavailable. Retention is seven days for eligible finalized recording clips only; archive clips are indefinite.
- Archive copies use temp file + flush/fsync + size comparison + ffprobe + optional checksum + atomic rename. Delete the source only after verified publication. Preserve source on any uncertainty.
- Dashboard only enqueues operations; filesystem mutations belong to recorder/archive/maintenance owners.
- Use SQLite transactions and conditional state transitions to prevent retention/archive/delete races and duplicate claims.
- Setup/reconfigure must validate the complete candidate, atomically replace protected TOML, preserve rollback, mask secrets, and only restart affected components.
- Preview is explicit, sub-stream-only, copy/remux-only by default, bounded by timeout/concurrency, and must never affect recording.

Your first response should report the current branch/status and identify concrete gaps or failing tests. Then implement and verify the highest-priority fixes. Test statically where possible and clearly distinguish Debian-only tests that require mounted drives, FFmpeg, systemd, Xfce, or reachable cameras. Do not commit secrets. If changes are made, update documentation/tests as appropriate and provide exact Debian commands for deployment. Do not assume the cameras are connected during installation; the wizard should configure them and the recorder should retry until they become reachable.
