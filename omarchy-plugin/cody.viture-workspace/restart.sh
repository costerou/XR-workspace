#!/usr/bin/env bash
set -euo pipefail
# Keep the restart independent of the QML instance being reloaded.
if ! systemctl --user restart viture-workspace-controller.service; then
    notify-send 'XR Workspace' 'Controller restart failed. Check the user service logs.' || true
    exit 1
fi
omarchy-shell shell rescanPlugins
