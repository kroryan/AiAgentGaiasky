#!/usr/bin/env python3
"""
Enables Gaia Sky's REST API in its config.yaml, if it is not already enabled.
Idempotent: does nothing (and exits 0) if a positive restPort is already set.
Used by install.sh / install.bat; also runnable standalone.
"""

import os
import platform
import re
import shutil
import sys
import time
from pathlib import Path


def config_path() -> Path:
    system = platform.system()
    if system == "Windows":
        return Path(os.environ.get("USERPROFILE", str(Path.home()))) / ".gaiasky" / "config.yaml"
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "gaiasky" / "config.yaml"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "gaiasky" / "config.yaml"


def main() -> int:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 30007
    path = config_path()

    if not path.exists():
        print(f"Gaia Sky config not found at {path}. Run Gaia Sky at least once, then re-run this.")
        return 1

    text = path.read_text(encoding="utf-8")
    match = re.search(r"^(\s*restPort:\s*)(-?\d+)\s*$", text, re.MULTILINE)
    if not match:
        print(f"Could not find 'restPort' in {path}; leaving it untouched.")
        return 1

    current = int(match.group(2))
    if current > 0:
        print(f"REST API already enabled on port {current}. Nothing to do.")
        return 0

    backup = path.with_name(path.name + f".bak-{int(time.time())}")
    shutil.copy2(path, backup)
    new_text = text[: match.start()] + f"{match.group(1)}{port}" + text[match.end() :]
    path.write_text(new_text, encoding="utf-8")
    print(f"Enabled Gaia Sky's REST API on port {port}.")
    print(f"(Previous config backed up to {backup}.)")
    print("Restart Gaia Sky if it is currently running, for this to take effect.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
