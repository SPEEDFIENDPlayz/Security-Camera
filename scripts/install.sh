#!/bin/sh
set -eu
test "$(id -u)" = 0 || { echo "Run as root" >&2; exit 1; }
ROOT=/opt/security-camera
id securitycam >/dev/null 2>&1 || useradd --system --home /var/lib/security-camera --shell /usr/sbin/nologin securitycam
install -d -m 0750 -o root -g securitycam /etc/security-camera
apt-get update
apt-get install -y ffmpeg python3 python3-venv
install -d -m 0755 "$ROOT"
cp -a . "$ROOT"
python3 -m venv "$ROOT/.venv"
"$ROOT/.venv/bin/pip" install --upgrade pip
"$ROOT/.venv/bin/pip" install "$ROOT"
install -d -m 0750 -o securitycam -g securitycam /var/lib/security-camera
test -f /etc/security-camera/config.toml || install -m 0640 -o root -g securitycam config/config.example.toml /etc/security-camera/config.toml
install -m 0644 systemd/*.service systemd/*.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now security-camera-recorder.service security-camera-archive.service security-camera-dashboard.service security-camera-control.service security-camera-maintenance.timer
echo "Edit /etc/security-camera/config.toml with real mount identities and camera URLs, then restart services."
