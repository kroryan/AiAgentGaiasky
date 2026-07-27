"""Terminal REPL for the agent, useful for testing the core (gaiasky.py, tools.py,
llm.py, agent.py) without the Qt overlay."""

from __future__ import annotations

import argparse
import sys
import threading

from .agent import Agent, Listener
from .config import AppConfig
from .gaiasky import GaiaSkyClient
from .llm import ApiType, LLMConfig
from .tools import ToolRegistry


def main(argv: list[str] | None = None) -> int:
    config = AppConfig.load()

    parser = argparse.ArgumentParser(description="Gaia Sky AI Agent — terminal REPL")
    parser.add_argument("--gaiasky", default=config.gaiasky_url, help="Gaia Sky REST base URL")
    parser.add_argument("--llm-api", choices=["ollama", "openai"], default=config.llm_api)
    parser.add_argument("--llm-url", default=config.llm_url)
    parser.add_argument("--model", default=config.llm_model)
    parser.add_argument("--api-key", default=config.llm_api_key)
    parser.add_argument("--no-wait", action="store_true", help="Do not wait for Gaia Sky to become ready")
    args = parser.parse_args(argv)

    gaiasky = GaiaSkyClient(base_url=args.gaiasky)
    print(f"Connecting to Gaia Sky at {args.gaiasky} ...")
    if not args.no_wait:
        if not gaiasky.wait_ready(timeout=60):
            print("Could not reach Gaia Sky (or its GUI has not finished starting). "
                  "Make sure it is running with the REST API enabled "
                  "(program -> net -> restPort in config.yaml), then try again.")
            return 1
    print("Connected.\n")

    registry = ToolRegistry(gaiasky)
    llm_config = LLMConfig(
        api=ApiType(args.llm_api), url=args.llm_url, model=args.model, api_key=args.api_key,
        temperature=config.llm_temperature, timeout=config.llm_timeout, max_tool_calls=config.max_tool_calls,
    )
    agent = Agent(registry, llm_config, system_prompt_extra=config.system_prompt)

    done = threading.Event()

    def on_assistant_message(message: str) -> None:
        print(f"\nassistant> {message}\n")

    def on_tool_call(name: str, summary: str, mutating: bool) -> None:
        print(f"  [tool] {summary}")

    def on_tool_result(name: str, result: str, error: bool) -> None:
        tag = "error" if error else "result"
        print(f"  [{tag}] {result}")

    def on_error(message: str) -> None:
        print(f"\n[error] {message}\n")

    def on_finished() -> None:
        done.set()

    listener = Listener(on_assistant_message, on_tool_call, on_tool_result, on_error, on_finished)

    print("Type a message and press enter. Ctrl-C or 'exit' to quit.\n")
    try:
        while True:
            try:
                user_message = input("you> ").strip()
            except EOFError:
                break
            if not user_message:
                continue
            if user_message.lower() in ("exit", "quit"):
                break
            done.clear()
            agent.send(user_message, listener)
            done.wait()
    except KeyboardInterrupt:
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
