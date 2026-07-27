"""Entry point: dispatches to the terminal REPL or the Qt overlay."""

from __future__ import annotations

import argparse
import sys

from .config import AppConfig


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="gaiasky-ai-agent",
        description="An external AI assistant for Gaia Sky, driven entirely over its REST API.",
    )
    parser.add_argument("--cli", action="store_true", help="Run the terminal REPL instead of the overlay UI")
    parser.add_argument("--window", action="store_true", help="Use a normal window instead of a frameless overlay")
    parser.add_argument("--gaiasky", default=None, help="Gaia Sky REST base URL, e.g. http://localhost:30007")
    args, remaining = parser.parse_known_args(argv)

    if args.cli:
        from . import cli
        cli_argv = list(remaining)
        if args.gaiasky:
            cli_argv = ["--gaiasky", args.gaiasky] + cli_argv
        return cli.main(cli_argv)

    from . import ui
    config = AppConfig.load()
    if args.gaiasky:
        config.gaiasky_url = args.gaiasky
    if args.window:
        config.window_mode = True
    return ui.run(config)


if __name__ == "__main__":
    sys.exit(main())
