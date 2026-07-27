"""
Client for chat-completion endpoints with tool-calling support.

Ported from `gaiasky.ai.AiClient`. Handles both the native Ollama API and any
OpenAI-compatible endpoint, normalizing the differences between the two into
a single request/response shape (see PLAN.md section 7).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import requests

from .tools import Tool


class ApiType(Enum):
    OLLAMA = "ollama"
    OPENAI = "openai"


class LLMError(Exception):
    """Raised on transport failure or a non-2xx response, with a human-readable message."""


@dataclass
class LLMConfig:
    api: ApiType = ApiType.OLLAMA
    url: str = "http://localhost:11434"
    model: str = "llama3.1"
    api_key: str | None = None
    temperature: float = 0.7
    timeout: float = 120.0
    max_tool_calls: int = 0  # 0 = unlimited


@dataclass
class Message:
    role: str  # "system", "user", "assistant" or "tool"
    content: str | None
    tool_calls: Any = None
    tool_call_id: str | None = None
    tool_name: str | None = None

    @staticmethod
    def system(content: str) -> "Message":
        return Message("system", content)

    @staticmethod
    def user(content: str) -> "Message":
        return Message("user", content)

    @staticmethod
    def assistant(content: str | None, tool_calls: Any = None) -> "Message":
        return Message("assistant", content, tool_calls=tool_calls)

    @staticmethod
    def tool(content: str, tool_call_id: str | None, tool_name: str | None) -> "Message":
        return Message("tool", content, tool_call_id=tool_call_id, tool_name=tool_name)


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict[str, Any]


@dataclass
class Response:
    content: str
    tool_calls: list[ToolCall]
    raw: dict[str, Any] = field(default_factory=dict)


# Known endpoint suffixes: a configured URL ending in any of these is treated as a
# full endpoint rather than a base, so the corresponding base can be recovered.
_ENDPOINT_SUFFIXES = ("/v1/chat/completions", "/chat/completions", "/api/chat", "/v1/models", "/api/tags")


def resolve_endpoint(url: str, path: str) -> str:
    """Builds a full endpoint URL from the user-supplied server URL, accepted in
    whichever form the user finds natural (bare host, path prefix, or a full
    endpoint copied from documentation)."""
    base = (url or "").strip().rstrip("/")
    if not base:
        raise LLMError("The inference server URL is empty. Set it in the settings.")
    if "://" not in base:
        base = "http://" + base
    for suffix in _ENDPOINT_SUFFIXES:
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    base = base.rstrip("/")
    if path.startswith("/v1/") and base.endswith("/v1"):
        base = base[: -len("/v1")]
    return base + path


class LLMClient:
    def __init__(self, config: LLMConfig):
        self.config = config

    def chat(self, messages: list[Message], tools: list[Tool]) -> Response:
        openai = self.config.api == ApiType.OPENAI
        body = self._build_request(messages, tools, openai)
        endpoint = resolve_endpoint(self.config.url, "/v1/chat/completions" if openai else "/api/chat")
        headers = {"Content-Type": "application/json"}
        if self.config.api_key and self.config.api_key.strip():
            headers["Authorization"] = "Bearer " + self.config.api_key.strip()

        try:
            response = requests.post(endpoint, json=body, headers=headers,
                                      timeout=max(10.0, self.config.timeout))
        except requests.exceptions.ConnectionError as e:
            raise LLMError(f"Could not reach the inference server at {self.config.url}. Check that it is "
                            f"running and that the URL in the settings is correct.") from e
        except requests.exceptions.Timeout as e:
            raise LLMError("The inference server did not respond in time. It may be loading the model, "
                            "or the timeout may be too low.") from e

        if not (200 <= response.status_code < 300):
            raise LLMError(self._describe_http_failure(response.status_code, response.text))
        return self._parse_response(response.json(), openai)

    def list_models(self) -> list[str]:
        openai = self.config.api == ApiType.OPENAI
        endpoint = resolve_endpoint(self.config.url, "/v1/models" if openai else "/api/tags")
        headers = {}
        if self.config.api_key and self.config.api_key.strip():
            headers["Authorization"] = "Bearer " + self.config.api_key.strip()
        try:
            response = requests.get(endpoint, headers=headers, timeout=20)
        except requests.exceptions.RequestException as e:
            raise LLMError(f"Could not reach the inference server at {self.config.url}.") from e
        if not (200 <= response.status_code < 300):
            raise LLMError(self._describe_http_failure(response.status_code, response.text))
        root = response.json()
        names = []
        listing = root.get("data", []) if openai else root.get("models", [])
        for node in listing:
            name = node.get("id" if openai else "name", "")
            if name:
                names.append(name)
        return names

    def _describe_http_failure(self, status: int, body: str) -> str:
        detail = self._extract_error_message(body)
        if status in (400, 404, 422):
            if "model" in detail.lower():
                return (f"The server rejected the model '{self.config.model}'. Check the model name in the "
                        f"settings, and use the refresh button there to list what the server offers. "
                        f"The server said: {detail}")
            return f"The server rejected the request (HTTP {status}): {detail}"
        if status in (401, 403):
            return f"The server refused the request (HTTP {status}). Check the API key in the settings. " \
                   f"The server said: {detail}"
        if status == 413:
            return "The conversation grew too large for the server to accept. Start a new conversation, " \
                   "or lower the tool call limit in the settings."
        if status == 429:
            return "The server is rate limiting the request. Wait a moment and try again."
        if status >= 500:
            return f"The inference server failed while handling the request (HTTP {status}). This is a " \
                   f"problem on the server, not in this application. Its response was: {detail}"
        return f"The server returned HTTP {status}: {detail}"

    @staticmethod
    def _extract_error_message(body: str) -> str:
        if not body or not body.strip():
            return "no further detail was given"
        try:
            node = json.loads(body)
            error = node.get("error")
            if isinstance(error, str):
                return error
            if isinstance(error, dict) and error.get("message"):
                return error["message"]
            if node.get("message"):
                return node["message"]
        except (json.JSONDecodeError, AttributeError):
            pass
        return body if len(body) <= 300 else body[:300] + "..."

    def _build_request(self, messages: list[Message], tools: list[Tool], openai: bool) -> dict:
        root: dict[str, Any] = {"model": self.config.model, "stream": False}
        message_array = []
        for m in messages:
            node: dict[str, Any] = {"role": m.role, "content": m.content or ""}
            if m.tool_calls:
                node["tool_calls"] = m.tool_calls
            if m.role == "tool":
                if openai and m.tool_call_id:
                    node["tool_call_id"] = m.tool_call_id
                elif not openai and m.tool_name:
                    # Ollama matches tool results by name rather than by call id.
                    node["tool_name"] = m.tool_name
            message_array.append(node)
        root["messages"] = message_array

        if tools:
            root["tools"] = self._build_tool_schemas(tools)

        if openai:
            root["temperature"] = self.config.temperature
        else:
            root["options"] = {"temperature": self.config.temperature}
        return root

    @staticmethod
    def _build_tool_schemas(tools: list[Tool]) -> list[dict]:
        schemas = []
        for tool in tools:
            properties = {}
            required = []
            for p in tool.parameters:
                properties[p.name] = {"type": p.type, "description": p.description}
                if p.required:
                    required.append(p.name)
            schemas.append({
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": {"type": "object", "properties": properties, "required": required},
                },
            })
        return schemas

    @staticmethod
    def _parse_response(root: dict, openai: bool) -> Response:
        error = root.get("error")
        if error:
            message = error if isinstance(error, str) else error.get("message", str(error))
            raise LLMError(message)

        message = (root.get("choices", [{}])[0].get("message", {}) if openai else root.get("message", {}))
        if not message:
            raise LLMError(f"Malformed response, no message found: {str(root)[:300]}")

        content = message.get("content") or ""
        calls = []
        for index, call in enumerate(message.get("tool_calls") or []):
            fn = call.get("function", {})
            name = fn.get("name", "")
            if not name:
                continue
            arguments = fn.get("arguments")
            if isinstance(arguments, str):
                text = arguments.strip()
                args = json.loads(text) if text else {}
            elif isinstance(arguments, dict):
                args = arguments
            else:
                args = {}
            call_id = call.get("id") or f"call_{index}"
            calls.append(ToolCall(call_id, name, args))
        return Response(content, calls, message)
