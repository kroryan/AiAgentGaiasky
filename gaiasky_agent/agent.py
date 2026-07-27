"""
Drives the conversation with the language model, executing any tools it requests
and feeding the results back until it produces a final answer.

Ported from `gaiasky.ai.AiAgent`. Everything runs on a dedicated worker thread:
the model call is a blocking network request and several tools block on Gaia
Sky's own blocking (`sync=true`) REST calls, neither of which may run on a UI
thread. Progress is reported through a `Listener`, whose callbacks fire from
that worker thread; a Qt UI must marshal them back onto the UI thread itself
(see ui.py, which does this with signals).
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Callable

from . import store
from .gaiasky import GaiaSkyError
from .llm import LLMClient, LLMConfig, LLMError, Message
from .tools import ToolError, ToolRegistry, system_prompt

logger = logging.getLogger(__name__)

# How many times one tool may be used in a row, with nothing else in between,
# before the run is broken. Does not bound how much work an exchange may do:
# a tour alternates between flying and explaining, so it never trips this.
MAX_CONSECUTIVE_CALLS = 6


@dataclass
class Listener:
    on_assistant_message: Callable[[str], None] = lambda message: None
    on_tool_call: Callable[[str, str, bool], None] = lambda name, summary, mutating: None
    on_tool_result: Callable[[str, str, bool], None] = lambda name, result, error: None
    on_error: Callable[[str], None] = lambda message: None
    on_finished: Callable[[], None] = lambda: None


class Agent:
    def __init__(self, registry: ToolRegistry, llm_config: LLMConfig, system_prompt_extra: str = ""):
        self.registry = registry
        self.llm_config = llm_config
        self.system_prompt_extra = system_prompt_extra
        self.history: list[Message] = []
        self._history_lock = threading.Lock()
        self._running = threading.Event()
        self._cancelled = threading.Event()
        self.conversation = store.Conversation.create()
        self._worker: threading.Thread | None = None

    def is_running(self) -> bool:
        return self._running.is_set()

    def cancel(self) -> None:
        """Requests cancellation of the exchange in progress. Any Gaia Sky animation
        being waited on is abandoned via the registry's stop flag."""
        if self._running.is_set():
            self._cancelled.set()
            self.registry.interrupt()

    def reset(self) -> None:
        """Discards the conversation history and starts a new, unsaved conversation."""
        with self._history_lock:
            self.history.clear()
            self.conversation = store.Conversation.create()

    def load(self, conversation: store.Conversation) -> None:
        """Replaces the loaded conversation with a stored one. The stored system turn
        is replaced with a freshly built one, so a resumed conversation reflects the
        tools and instructions in force now rather than those of the day it started."""
        with self._history_lock:
            self.conversation = conversation
            self.history = [Message.system(system_prompt(self.system_prompt_extra))]
            self.history.extend(m for m in conversation.messages if m.role != "system")

    def persist(self) -> None:
        with self._history_lock:
            self.conversation.messages = list(self.history)
            import time
            self.conversation.updated = time.time()
            if self.conversation.title is None and self.conversation.messages:
                for m in self.conversation.messages:
                    if m.role == "user" and m.content:
                        self.conversation.title = store.title_from(m.content)
                        break
            snapshot = self.conversation
        store.save(snapshot)

    def send(self, user_message: str, listener: Listener) -> None:
        """Sends a user message and starts working on it in a worker thread. Returns
        at once; progress arrives through the listener. Does nothing if an exchange
        is already in progress."""
        if self._running.is_set():
            return
        self._running.set()
        self._cancelled.clear()
        self.registry.resume()

        def body() -> None:
            try:
                self._run(user_message, listener)
            except Exception as e:
                if self._cancelled.is_set():
                    logger.debug("The exchange was cancelled by the user.")
                else:
                    logger.exception("Exchange failed")
                    listener.on_error(self._describe(e))
            finally:
                self._running.clear()
                self.persist()
                listener.on_finished()

        self._worker = threading.Thread(target=body, name="gaiasky-ai-agent", daemon=True)
        self._worker.start()

    def _run(self, user_message: str, listener: Listener) -> None:
        client = LLMClient(self.llm_config)
        tools = self.registry.tools()

        with self._history_lock:
            if not self.history:
                self.history.append(Message.system(system_prompt(self.system_prompt_extra)))
            self.history.append(Message.user(user_message))
            turns = list(self.history)

        max_rounds = self.llm_config.max_tool_calls if self.llm_config.max_tool_calls > 0 else 10_000_000
        recent: list[str] = []

        for _ in range(max_rounds):
            if self._cancelled.is_set():
                return

            response = client.chat(turns, tools)

            if not response.tool_calls:
                content = (response.content or "").strip()
                if not content:
                    content = self._final_answer(client, turns)
                self._emit_assistant(content, turns, listener)
                return

            assistant_turn = Message.assistant(response.content, response.raw.get("tool_calls"))
            turns.append(assistant_turn)
            with self._history_lock:
                self.history.append(assistant_turn)

            if response.content and response.content.strip():
                listener.on_assistant_message(response.content.strip())

            travelled = False
            for call in response.tool_calls:
                if self._cancelled.is_set():
                    return
                refusal = self._refuse(call, travelled, recent)
                if refusal is not None:
                    result = refusal
                    listener.on_tool_result(call.name, result, False)
                else:
                    result = self._execute(call, listener)
                    travelled = travelled or self.registry.is_exclusive(call.name)
                tool_message = Message.tool(result, call.id, call.name)
                turns.append(tool_message)
                with self._history_lock:
                    self.history.append(tool_message)

        self._emit_assistant(self._final_answer(client, turns), turns, listener)

    def _refuse(self, call, travelled: bool, recent: list[str]) -> str | None:
        """Declines a call rather than performing it, when doing so would not advance
        the task: a second move before the first has been described, an immediate
        repeat of the call just made, or a long run of one tool with nothing between."""
        if travelled and self.registry.is_exclusive(call.name):
            return ("Not performed. The camera has just arrived somewhere else. Describe what is on screen "
                    "there first, then request this move in your next turn.")

        signature = self._summarize(call)
        if recent and recent[-1] == signature:
            return ("Not performed. This is the very call you just made, so it would change nothing. "
                    "Move on to the next step, or answer the user now.")

        run = 0
        for entry in reversed(recent):
            if entry.startswith(call.name + "("):
                run += 1
            else:
                break
        if run >= MAX_CONSECUTIVE_CALLS:
            return (f"Not performed. You have called '{call.name}' {run} times in a row with nothing "
                    f"in between, which suggests you are stuck. Do the next part of the task with a "
                    f"different tool, or answer the user now.")

        recent.append(signature)
        while len(recent) > MAX_CONSECUTIVE_CALLS + 1:
            recent.pop(0)
        return None

    def _final_answer(self, client: LLMClient, turns: list[Message]) -> str:
        """Asks the model for a closing answer with no tools advertised, used when the
        model falls silent mid-exchange, and when the tool budget runs out."""
        closing = list(turns)
        closing.append(Message.user(
            "Answer now, in prose, using what you have already done and found out. Do not call any more tools. "
            "Write in the same language the user has been writing in."))
        try:
            response = client.chat(closing, [])
            content = (response.content or "").strip()
            if content:
                return content
        except LLMError:
            logger.debug("final_answer request failed", exc_info=True)
        return ("I carried out the actions above, but could not put together a closing answer. "
                "Ask me again and I will describe what I found.")

    def _emit_assistant(self, content: str, turns: list[Message], listener: Listener) -> None:
        assistant = Message.assistant(content, None)
        turns.append(assistant)
        with self._history_lock:
            self.history.append(assistant)
        listener.on_assistant_message(content)

    def _execute(self, call, listener: Listener) -> str:
        tool = self.registry.get(call.name)
        if tool is None:
            message = f"No such tool: '{call.name}'."
            listener.on_tool_result(call.name, message, True)
            return message

        listener.on_tool_call(call.name, self._summarize(call), tool.mutating)
        try:
            result = tool.function(call.args or {})
            listener.on_tool_result(call.name, result, False)
            return result
        except Exception as e:
            if self._cancelled.is_set():
                return "The user cancelled this action."
            message = "Error: " + self._describe(e)
            listener.on_tool_result(call.name, message, True)
            return message

    @staticmethod
    def _summarize(call) -> str:
        args = call.args or {}
        if not args:
            return call.name
        return call.name + "(" + ", ".join(f"{k}={v}" for k, v in args.items()) + ")"

    @staticmethod
    def _describe(e: Exception) -> str:
        if isinstance(e, ToolError):
            return str(e)
        if isinstance(e, GaiaSkyError):
            return str(e)
        if isinstance(e, LLMError):
            return str(e)
        message = str(e)
        return message if message else type(e).__name__
