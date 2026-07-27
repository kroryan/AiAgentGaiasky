#!/usr/bin/env bash
#
# One-step installer for Linux/macOS: creates a virtual environment, installs
# dependencies, enables Gaia Sky's REST API if it isn't already, and sets the
# assistant to start automatically with your desktop session (a systemd --user
# service where available, otherwise an XDG autostart entry).
#
# Safe to re-run: every step is skipped if it was already done.
#
# Usage:
#   ./install.sh
#   GAIASKY_PORT=30010 ./install.sh   # use a different REST port

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

GAIASKY_PORT="${GAIASKY_PORT:-30007}"

echo "== Gaia Sky AI Agent installer =="
echo

if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 was not found on PATH. Install Python 3.10+ and re-run this script." >&2
    exit 1
fi

if [ ! -d .venv ]; then
    echo "Creating virtual environment (.venv)..."
    python3 -m venv .venv
else
    echo "Virtual environment already exists."
fi

echo "Installing dependencies..."
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r requirements.txt

echo
echo "Checking Gaia Sky's REST API..."
.venv/bin/python scripts/enable_rest_api.py "$GAIASKY_PORT" || true

PYTHON_BIN="$SCRIPT_DIR/.venv/bin/python"
RUN_PY="$SCRIPT_DIR/run.py"

echo
echo "Setting up autostart..."

if command -v systemctl >/dev/null 2>&1 && systemctl --user show-environment >/dev/null 2>&1; then
    UNIT_DIR="$HOME/.config/systemd/user"
    UNIT_FILE="$UNIT_DIR/gaiasky-ai-agent.service"
    mkdir -p "$UNIT_DIR"

    NEW_UNIT="[Unit]
Description=Gaia Sky AI Agent overlay
After=graphical-session.target

[Service]
Type=simple
WorkingDirectory=$SCRIPT_DIR
ExecStart=$PYTHON_BIN $RUN_PY
Restart=on-failure
RestartSec=5
Environment=DISPLAY=:0.0

[Install]
WantedBy=graphical-session.target
"

    if [ -f "$UNIT_FILE" ] && [ "$(cat "$UNIT_FILE")" = "$NEW_UNIT" ]; then
        echo "systemd user service already installed and up to date."
        systemctl --user daemon-reload
        systemctl --user enable --now gaiasky-ai-agent.service >/dev/null 2>&1 || true
    else
        printf '%s' "$NEW_UNIT" > "$UNIT_FILE"
        systemctl --user daemon-reload
        systemctl --user enable gaiasky-ai-agent.service
        # restart, not "enable --now": "start" is a no-op on an already-running unit,
        # which would otherwise leave a stale process running with the old ExecStart.
        systemctl --user restart gaiasky-ai-agent.service
        echo "Installed and (re)started the systemd user service 'gaiasky-ai-agent'."
    fi
    echo
    echo "Manage it with:"
    echo "  systemctl --user status gaiasky-ai-agent"
    echo "  systemctl --user stop gaiasky-ai-agent"
    echo "  systemctl --user disable --now gaiasky-ai-agent   # to remove autostart"
else
    AUTOSTART_DIR="$HOME/.config/autostart"
    DESKTOP_FILE="$AUTOSTART_DIR/gaiasky-ai-agent.desktop"
    mkdir -p "$AUTOSTART_DIR"

    if [ -f "$DESKTOP_FILE" ]; then
        echo "Autostart entry already exists at $DESKTOP_FILE."
    else
        cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=Gaia Sky AI Agent
Exec=$PYTHON_BIN $RUN_PY
X-GNOME-Autostart-enabled=true
EOF
        echo "Installed an autostart entry at $DESKTOP_FILE."
    fi
fi

echo
echo "Done. The assistant will start automatically at your next login, and will wait"
echo "for Gaia Sky whenever you start it."
echo
echo "To start it right now instead of waiting for next login:"
echo "  $PYTHON_BIN $RUN_PY &"
