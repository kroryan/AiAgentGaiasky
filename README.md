# Gaia Sky AI Agent

An AI assistant for [Gaia Sky](https://gaiasky.space) that lives **entirely outside** it.

This project talks to a running Gaia Sky instance only through its built-in REST API
(`APIv2`). It does not require, assume, or depend on any patch, fix, or modification to
Gaia Sky itself — it works against the official Linux and Windows builds as they ship.
The chat window is a separate, frameless, always-on-top overlay that sits visually on
top of Gaia Sky, so it reads as part of the application without being part of it.

See [docs/PLAN.md](docs/PLAN.md) for the full design rationale, [docs/INSTALL.md](docs/INSTALL.md)
for installation and autostart, and [docs/GUIDE.md](docs/GUIDE.md) for day-to-day usage.

## Why this exists

This project reimplements, as a standalone external application, an AI agent that was
originally proposed as an in-app feature for Gaia Sky (tool registry, agentic loop, LLM
backends, chat UI). That PR was not accepted upstream: Gaia Sky's maintainer considered
a full in-process AI agent out of scope for a visualization engine, and asked that any
such harness live outside the project and use the existing REST API instead. This repo
is that external harness.

## Features

- **~55 tools** covering navigation (fly to / land on / focus / track objects), time
  control, visibility toggles, dataset management, rendering adjustments, on-screen
  text, and more — the same catalog of actions the original in-app assistant had, plus
  a few of this project's own: bulk object verification for planning long tours, and
  saving notes/lists to plain text files that outlive the chat.
- **Long tours just work**: asking for a tour of dozens of objects (a list of nebulae,
  every planet and moon, etc.) is not capped — the agent narrates between stops and
  keeps going for as many as were asked for.
- **Two LLM backends**: a local [Ollama](https://ollama.com) server (native API) or any
  OpenAI-compatible endpoint (OpenAI itself, LM Studio, vLLM, Ollama Cloud, etc.).
- **Overlay UI**: frameless, translucent, always-on-top panel with Markdown rendering
  (real tables, not monospace text), a connection indicator, and a Stop button.
- **Conversation history**: conversations are saved locally and can be resumed.
- **Terminal mode** (`--cli`) for testing the agent without the GUI.

## Quick start

```bash
pip install -r requirements.txt

# 1. Enable the REST API in Gaia Sky's config.yaml (see GUIDE.md), then start Gaia Sky.
# 2. Run the assistant:
python run.py
```

## Requirements

- Python 3.10+
- A running Gaia Sky instance (official Linux AppImage or Windows build) with its REST
  API enabled
- Either a local [Ollama](https://ollama.com) installation, or access to an
  OpenAI-compatible inference endpoint

## Security note

Gaia Sky's own REST API documentation warns that it can permit remote code execution
and listens on all network interfaces, not just localhost. This application only ever
connects to `localhost` by default, but **you** are responsible for not exposing Gaia
Sky's REST port to an untrusted network. See GUIDE.md for details.

## License

MPL-2.0, matching the Gaia Sky ecosystem. See [LICENSE](LICENSE).

## Status

Early, external, community project. Not affiliated with, endorsed by, or supported by
the Gaia Sky project or its maintainers.
