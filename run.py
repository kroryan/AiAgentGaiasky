#!/usr/bin/env python3
"""Entry point for development (`python run.py`) and for PyInstaller packaging."""

import sys

from gaiasky_agent.main import main

if __name__ == "__main__":
    sys.exit(main())
