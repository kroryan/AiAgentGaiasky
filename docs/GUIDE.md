# Guide: installation, configuration and usage

This guide covers everything needed to get the Gaia Sky AI Agent running against an
official, unmodified Gaia Sky build.

## 1. Enable Gaia Sky's REST API

Gaia Sky ships with its REST server disabled. It is a single line in its configuration
file:

- **Linux** (AppImage or package install): `~/.config/gaiasky/config.yaml`
  (or `$XDG_CONFIG_HOME/gaiasky/config.yaml` if you have `XDG_CONFIG_HOME` set)
- **Windows**: `%USERPROFILE%\.gaiasky\config.yaml`
- **macOS**: `~/Library/Application Support/gaiasky/config.yaml`

Open it and find the `program → net → restPort` key:

```yaml
program:
  net:
    # Enable the REST API on this TCP port (negative to disable).
    restPort: -1
```

Change `-1` to a free port in the 1024–49151 range, for example:

```yaml
    restPort: 30007
```

Save the file and start (or restart) Gaia Sky. Its log will show:

```
Starting REST APIv2 server on http://localhost:30007/apiv2/
```

The server only starts answering once the GUI has fully finished loading; a request
made before that gets `"GUI not yet initialized"`, and this application waits for that
automatically.

You can check it works by opening `http://localhost:30007/apiv2/help` in a browser once
Gaia Sky is running — it should return a small JSON document.

### Security

Gaia Sky's own log prints this warning when the REST server starts:

> *Warning: REST API server may permit remote code execution! Only use this
> functionality in a trusted environment!*

The server listens on **all network interfaces**, not just `localhost`, and Gaia Sky
does not add authentication of its own. This application always talks to `localhost`
(or whatever host you configure), but it is your responsibility to make sure the port
is not reachable from an untrusted network — keep it behind your firewall, or only
enable it while you are using the assistant.

## 2. Install the assistant

```bash
git clone <this-repo-url> gaiaskyAIagent
cd gaiaskyAIagent
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Requirements: Python 3.10+, `PyQt6`, `requests`.

## 3. Set up a language model backend

You need something to talk to. Two options are supported:

### Option A — Ollama (local, native API)

1. Install [Ollama](https://ollama.com) and pull a model that supports tool calling,
   for example: `ollama pull qwen2.5:14b` or `ollama pull llama3.1`.
2. In the assistant's settings, set backend to **ollama** and the server URL to
   `http://localhost:11434` (the default).

### Option B — OpenAI-compatible endpoint

Works with OpenAI itself, LM Studio, vLLM's OpenAI-compatible server, Ollama Cloud, or
any other server that speaks the `/v1/chat/completions` API with tool calling.

1. Set backend to **openai**.
2. Set the server URL (e.g. `https://api.openai.com` or your local server's address).
3. Set the API key, if the server requires one.
4. Set the model name (e.g. `gpt-4o-mini`).

Not every model supports tool calling well. If the assistant seems to only talk and
never act, try a different model.

## 4. Run it

With Gaia Sky already running and its REST API enabled:

```bash
python run.py
```

This opens the overlay panel. The first time, open its settings (the gear icon) and
fill in:

- **Gaia Sky URL**: `http://localhost:30007` (the port you set in step 1)
- **Backend**, **Server URL**, **API key**, **Model** as set up in step 3

Then just type. Try: *"Take me to Mars"*, or *"Give me a 5-stop tour of the solar
system, staying 10 seconds at each stop."*

### Command-line options

```
python run.py                 # overlay (frameless, translucent, always-on-top)
python run.py --window        # a normal window instead of an overlay
python run.py --gaiasky URL   # override the Gaia Sky REST URL for this run
python run.py --cli           # terminal REPL, no GUI — useful for testing
```

### Terminal mode

`python run.py --cli` runs the same agent without any GUI, printing tool calls and
results as they happen. Useful for verifying the connection and the model's tool use
before troubleshooting the overlay.

## 5. Using the overlay

- **Drag** the title bar to move the panel; **resize** from the bottom-right corner.
- The dot next to the title is the connection indicator: green means Gaia Sky answered
  the last ping, orange/red means it didn't.
- Replies are rendered as Markdown: headings, lists and **tables** render as real UI
  elements in the system font, not as a block of monospace text.
- Each tool call the assistant makes shows up as a small collapsible line; click it to
  see the full result.
- **Stop** cancels the current exchange. If the camera is mid-flight, it also issues a
  `camera/stop` call so the flight actually halts rather than just being abandoned.
- **＋** starts a new conversation. **☰** opens conversation history (open or delete
  past conversations). **⚙** opens settings.

### Known limitations of the overlay

- **Always-on-top** is reliable on Windows and on X11 desktops. On pure Wayland, most
  compositors do not let ordinary applications force themselves above others; if the
  panel gets buried, try running with `QT_QPA_PLATFORM=xcb` (XWayland), use your
  compositor's own "always on top" window rule, or use `--window` and place it manually.
- If Gaia Sky is running in **exclusive fullscreen**, no overlay window can appear above
  it — this is a general limitation of overlays (the same applies to Discord's or
  Steam's overlays), not specific to this application. Run Gaia Sky in borderless/
  maximized windowed mode instead.
- `--window` gives you a normal, non-transparent window if you would rather place it
  next to Gaia Sky than on top of it.

## 6. Packaging

### Windows

```bash
pip install pyinstaller
pyinstaller --noconsole --onefile --name GaiaSkyAIAgent run.py
```

Produces `dist/GaiaSkyAIAgent.exe`, a single-file executable.

### Linux

Run directly with a system Python (`python run.py`), or build a single-file binary the
same way:

```bash
pip install pyinstaller
pyinstaller --onefile --name gaiasky-ai-agent run.py
```

An AppImage wrapper is not provided yet; `python-appimage` is a reasonable path if
that's wanted later.

## 7. Known limitations, by design

This application deliberately never modifies Gaia Sky, which means a few tools work
slightly differently than they would with a patched Gaia Sky:

| Situation | What happens here |
|---|---|
| `search_objects` (fuzzy/partial name search) | Vanilla Gaia Sky has no REST-reachable fuzzy search (the original tool used an internal, in-process index). This tool tries a handful of exact-ish variants of the name (as typed, Title Case, UPPERCASE, common catalogue prefixes) and says plainly when it can't find a real match. |
| `get_closest_object` | Gaia Sky serializes the returned object with a generic Java JSON writer whose exact shape isn't guaranteed. This tool reads the name defensively and reports when it can't. |
| Camera commands issued rapidly over REST | Gaia Sky's REST handler invokes the camera API from a non-render thread, same as any script. There is a narrow, pre-existing thread-safety issue in Gaia Sky itself here (independent of this project), which the natural pace of an LLM-driven agent (one HTTP round-trip and one inference call between actions) makes very unlikely to hit in practice. Fixed upstream in Gaia Sky `master` as of commit `650fd82e3` (2026-07-29); the pacing mitigation here is harmless and stays in place for anyone running an older build. |
| A tool call whose underlying action throws an exception inside Gaia Sky | Gaia Sky's REST server catches the exception internally and still reports `success: true`. Where it matters, this application double-checks by querying the actual effect afterwards (e.g. checking distance-to-object after a flight) rather than trusting the bare success flag. |

None of these require or wait on any change to Gaia Sky; they're handled entirely on
this side of the REST boundary.

## 8. Troubleshooting

- **"Could not reach Gaia Sky"**: make sure Gaia Sky is running, `restPort` is set to a
  positive port in its `config.yaml`, and the URL in settings matches
  (`http://localhost:<port>`).
- **"GUI not yet initialized"** (visible in `--cli` mode or Gaia Sky's own log): Gaia
  Sky is still starting up. This application already waits and retries automatically;
  if it persists for a long time, check Gaia Sky's own log for errors during startup.
- **The model never calls tools, only talks**: not every local model supports tool
  calling reliably. Try a model documented to support it well (e.g. Qwen2.5, Llama 3.1,
  most current OpenAI models).
- **Overlay isn't visible over Gaia Sky**: see the Wayland/fullscreen notes in section 5.
