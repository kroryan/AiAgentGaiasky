#!/usr/bin/env python3
"""Blocks until Gaia Sky's REST API answers, or exits 1 on timeout. Used by the
launch-with-gaiasky wrapper scripts; also runnable standalone for diagnostics."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from gaiasky_agent.gaiasky import GaiaSkyClient  # noqa: E402


def main() -> int:
    url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:30007"
    timeout = float(sys.argv[2]) if len(sys.argv) > 2 else 180.0
    client = GaiaSkyClient(base_url=url)
    if client.wait_ready(timeout=timeout):
        print(f"Gaia Sky is ready at {url}.")
        return 0
    print(f"Gaia Sky did not become ready at {url} within {timeout:.0f}s.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
