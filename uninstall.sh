#!/usr/bin/env bash
#
# Reverses install.sh: stops and removes the autostart entry (systemd user service
# or XDG autostart .desktop file), and optionally removes the virtual environment.
# Does NOT touch Gaia Sky's config.yaml — restPort is left as it is, since other
# tools may also rely on it; re-run scripts/enable_rest_api.py's edit by hand if you
# want to disable it again (see the .bak file it left behind).
#
# Usage:
#   ./uninstall.sh            # removes autostart entry only
#   ./uninstall.sh --purge    # also deletes the .venv directory

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "== Gaia Sky AI Agent uninstaller =="
echo

UNIT_FILE="$HOME/.config/systemd/user/gaiasky-ai-agent.service"
if command -v systemctl >/dev/null 2>&1 && [ -f "$UNIT_FILE" ]; then
    echo "Stopping and removing the systemd user service..."
    systemctl --user disable --now gaiasky-ai-agent.service 2>/dev/null || true
    rm -f "$UNIT_FILE"
    systemctl --user daemon-reload
    echo "Removed $UNIT_FILE."
else
    pkill -TERM -f "$SCRIPT_DIR/run.py" 2>/dev/null || true
fi

DESKTOP_FILE="$HOME/.config/autostart/gaiasky-ai-agent.desktop"
if [ -f "$DESKTOP_FILE" ]; then
    rm -f "$DESKTOP_FILE"
    echo "Removed $DESKTOP_FILE."
fi

if [ "${1:-}" = "--purge" ]; then
    if [ -d .venv ]; then
        rm -rf .venv
        echo "Removed the virtual environment (.venv)."
    fi
fi

echo
echo "Done. The assistant will no longer start automatically."
if [ "${1:-}" != "--purge" ]; then
    echo "The virtual environment (.venv) was left in place; re-run with --purge to remove it too."
fi
echo "Gaia Sky's config.yaml was left untouched (restPort is still enabled)."
