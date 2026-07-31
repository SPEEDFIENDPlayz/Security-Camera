#!/bin/sh
set -eu
systemctl --no-pager --full status security-camera-recorder.service security-camera-archive.service security-camera-dashboard.service
systemctl --no-pager list-timers security-camera-maintenance.timer
