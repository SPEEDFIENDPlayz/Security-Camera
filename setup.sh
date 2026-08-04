#!/bin/sh
# Run this file from the unzipped project directory. It requests administrator
# approval, installs prerequisites, then starts the graphical setup wizard.
set -eu

SCRIPT_PATH=$(readlink -f "$0")
if [ "$(id -u)" -ne 0 ]; then
    command -v pkexec >/dev/null 2>&1 || { echo "PolicyKit (pkexec) is required." >&2; exit 1; }
    exec pkexec "$SCRIPT_PATH"
fi

SOURCE_ROOT=$(dirname "$SCRIPT_PATH")
ROOT=/opt/security-camera
id securitycam >/dev/null 2>&1 || useradd --system --home /var/lib/security-camera --shell /usr/sbin/nologin securitycam
apt-get update
apt-get install -y ffmpeg python3 python3-venv python3-tk policykit-1
install -d -m 0750 -o root -g securitycam /etc/security-camera
install -d -m 0750 -o securitycam -g securitycam /var/lib/security-camera
install -d -m 0755 "$ROOT"
tar --exclude=.git --exclude=.venv --exclude=__pycache__ -C "$SOURCE_ROOT" -cf - . | tar -C "$ROOT" -xf -
python3 -m venv "$ROOT/.venv"
"$ROOT/.venv/bin/pip" install --upgrade pip
"$ROOT/.venv/bin/pip" install "$ROOT"
install -m 0644 "$ROOT"/systemd/*.service "$ROOT"/systemd/*.timer /etc/systemd/system/
systemctl daemon-reload
# A prior incomplete installation must never run with placeholder settings.
systemctl disable --now security-camera-recorder.service security-camera-archive.service security-camera-dashboard.service security-camera-maintenance.timer 2>/dev/null || true
exec "$ROOT/.venv/bin/security-camera" setup
