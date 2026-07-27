#!/usr/bin/env bash
#
# Starts Gaia Sky and the AI Agent overlay together, as two independent processes.
# This is a convenience wrapper only: it does not modify Gaia Sky in any way, it just
# launches the AppImage (or binary) you already have, waits for its REST API to answer,
# and then starts the overlay pointed at it.
#
# Usage:
#   GAIASKY_BIN=/path/to/Gaiasky.AppImage ./launch-with-gaiasky.sh
#
# Configuration (environment variables, all optional):
#   GAIASKY_BIN     Path to the Gaia Sky executable/AppImage.
#                   Default: "gaiasky" (must be on PATH).
#   GAIASKY_URL     REST base URL to wait for and connect to.
#                   Default: http://localhost:30007
#   AGENT_DIR       Path to this repository's checkout.
#                   Default: directory this script lives in, one level up.

set -euo pipefail

GAIASKY_BIN="${GAIASKY_BIN:-gaiasky}"
GAIASKY_URL="${GAIASKY_URL:-http://localhost:30007}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_DIR="${AGENT_DIR:-$(dirname "$SCRIPT_DIR")}"

echo "Starting Gaia Sky ($GAIASKY_BIN)..."
"$GAIASKY_BIN" &
GAIASKY_PID=$!

cleanup() {
    if kill -0 "$GAIASKY_PID" 2>/dev/null; then
        echo "Stopping Gaia Sky (pid $GAIASKY_PID)..."
        kill "$GAIASKY_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT

echo "Waiting for Gaia Sky's REST API at $GAIASKY_URL ..."
python3 "$SCRIPT_DIR/wait_ready.py" "$GAIASKY_URL" 180

echo "Gaia Sky is ready. Starting the AI Agent overlay..."
cd "$AGENT_DIR"
python3 run.py --gaiasky "$GAIASKY_URL"
