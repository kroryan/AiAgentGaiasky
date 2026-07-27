"""Persistent application settings, stored as JSON in the user's config directory."""

from __future__ import annotations

import json
import os
import platform
from dataclasses import asdict, dataclass
from pathlib import Path


def config_dir() -> Path:
    """Where this app keeps its own settings and conversation history. Distinct from
    Gaia Sky's own config directory, which this app never touches."""
    system = platform.system()
    if system == "Windows":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return base / "GaiaSkyAIAgent"
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "GaiaSkyAIAgent"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "gaiaskyAIagent"


def _config_file() -> Path:
    return config_dir() / "config.json"


@dataclass
class AppConfig:
    # Gaia Sky connection.
    gaiasky_url: str = "http://localhost:30007"

    # LLM backend: "ollama" (native API) or "openai" (any OpenAI-compatible endpoint).
    llm_api: str = "ollama"
    llm_url: str = "http://localhost:11434"
    llm_model: str = "llama3.1"
    llm_api_key: str = ""
    llm_temperature: float = 0.7
    llm_timeout: float = 120.0
    max_tool_calls: int = 0  # 0 = unlimited

    # Extra text appended to the system prompt.
    system_prompt: str = ""

    # UI.
    overlay_opacity: float = 0.88
    font_size: int = 14
    window_mode: bool = False  # False = frameless overlay, True = a normal window

    @staticmethod
    def load() -> "AppConfig":
        path = _config_file()
        if not path.exists():
            return AppConfig()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return AppConfig()
        defaults = AppConfig()
        for key, value in data.items():
            if hasattr(defaults, key):
                setattr(defaults, key, value)
        return defaults

    def save(self) -> None:
        path = _config_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8")
