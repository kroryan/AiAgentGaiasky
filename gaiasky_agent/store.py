"""Conversation persistence: one JSON file per conversation, on disk."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .llm import Message


def _conversations_dir() -> Path:
    from .config import config_dir
    directory = config_dir() / "conversations"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


@dataclass
class Conversation:
    id: str
    title: str | None = None
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)
    messages: list[Message] = field(default_factory=list)

    @staticmethod
    def create() -> "Conversation":
        return Conversation(id=uuid.uuid4().hex[:12])

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "created": self.created,
            "updated": self.updated,
            "messages": [
                {
                    "role": m.role,
                    "content": m.content,
                    "tool_calls": m.tool_calls,
                    "tool_call_id": m.tool_call_id,
                    "tool_name": m.tool_name,
                }
                for m in self.messages
            ],
        }

    @staticmethod
    def from_dict(data: dict) -> "Conversation":
        messages = [
            Message(m["role"], m.get("content"), m.get("tool_calls"), m.get("tool_call_id"), m.get("tool_name"))
            for m in data.get("messages", [])
        ]
        return Conversation(
            id=data["id"], title=data.get("title"),
            created=data.get("created", time.time()), updated=data.get("updated", time.time()),
            messages=messages,
        )


def title_from(user_message: str) -> str:
    text = " ".join(user_message.split())
    return text[:60] + ("…" if len(text) > 60 else "")


def save(conversation: Conversation) -> None:
    path = _conversations_dir() / f"{conversation.id}.json"
    path.write_text(json.dumps(conversation.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")


def load(conversation_id: str) -> Conversation:
    path = _conversations_dir() / f"{conversation_id}.json"
    return Conversation.from_dict(json.loads(path.read_text(encoding="utf-8")))


def delete(conversation_id: str) -> None:
    path = _conversations_dir() / f"{conversation_id}.json"
    path.unlink(missing_ok=True)


def list_all() -> list[Conversation]:
    """All stored conversations, most recently updated first. Loads only metadata-bearing fields cheaply."""
    conversations = []
    for path in _conversations_dir().glob("*.json"):
        try:
            conversations.append(Conversation.from_dict(json.loads(path.read_text(encoding="utf-8"))))
        except (json.JSONDecodeError, KeyError, OSError):
            continue
    conversations.sort(key=lambda c: c.updated, reverse=True)
    return conversations
