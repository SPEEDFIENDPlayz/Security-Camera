#!/bin/sh
# Compatibility entry point. The graphical wizard is the supported installer.
exec "$(dirname "$0")/../setup.sh" "$@"
