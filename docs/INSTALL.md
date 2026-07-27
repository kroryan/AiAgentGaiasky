# Installation and autostart

This document covers two things: installing and running the agent today, and a
concrete path toward starting it automatically alongside Gaia Sky. Everything here
runs **outside** Gaia Sky — no file inside a Gaia Sky install is ever created, changed,
or depended on. "Autostart" is achieved the way any two independent desktop
applications are made to start together: OS-level launch mechanisms and a small
wrapper script, not a Gaia Sky plugin or dataset.

For day-to-day usage once it's running, see [GUIDE.md](GUIDE.md).

## 1. Manual installation

```bash
git clone <this-repo-url> gaiaskyAIagent
cd gaiaskyAIagent
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Enable Gaia Sky's REST API once (`program.net.restPort` in its `config.yaml`; see
GUIDE.md section 1 for exact paths and values). This is the only configuration change
involved anywhere, and it is a one-line setting Gaia Sky itself ships with, not a
patch. It only needs to be done once — Gaia Sky remembers it on every subsequent start.

Then, with Gaia Sky running:

```bash
python run.py
```

## 2. Starting both together with one command

`scripts/launch-with-gaiasky.sh` (Linux/macOS) and `scripts/launch-with-gaiasky.bat`
(Windows) start Gaia Sky, wait for its REST API to come up, and then start the
overlay — one command, two independent processes. Neither script touches Gaia Sky's
files; they only launch the executable you already have and poll its HTTP port.

```bash
# Linux/macOS
GAIASKY_BIN=/path/to/Gaiasky.AppImage ./scripts/launch-with-gaiasky.sh
```

```bat
:: Windows
set GAIASKY_BIN=C:\Path\To\Gaiasky.exe
scripts\launch-with-gaiasky.bat
```

Closing the wrapper script (Ctrl-C) also stops the Gaia Sky process it started, on
Linux/macOS. On Windows, close both windows independently.

## 3. Auto-starting with your desktop session

This makes the agent (and optionally Gaia Sky itself) launch automatically whenever
you log in — again, using only OS-native mechanisms aimed at this repository's own
`run.py`. Gaia Sky is not modified or made aware of this in any way.

### Linux (systemd user service)

Create `~/.config/systemd/user/gaiasky-ai-agent.service`:

```ini
[Unit]
Description=Gaia Sky AI Agent overlay
After=graphical-session.target

[Service]
Type=simple
WorkingDirectory=%h/gaiaskyAIagent
ExecStart=%h/gaiaskyAIagent/.venv/bin/python run.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=graphical-session.target
```

Adjust `WorkingDirectory`/`ExecStart` if you cloned this repository somewhere other
than `~/gaiaskyAIagent`. Then:

```bash
systemctl --user daemon-reload
systemctl --user enable --now gaiasky-ai-agent.service
```

The overlay now starts with your graphical session and restarts if it crashes. It
will simply wait (retrying the connection) until you start Gaia Sky, since the agent
already tolerates Gaia Sky not being up yet.

If you'd rather have Gaia Sky itself start automatically too, point `ExecStart` at
`scripts/launch-with-gaiasky.sh` instead, with `GAIASKY_BIN` set via an `Environment=`
line in the unit file.

### Windows (Startup folder)

1. Create a shortcut to `run.pyw` (a console-less variant — copy `run.py` to
   `run.pyw`, or use `pythonw.exe run.py` as the shortcut's target) or to
   `scripts\launch-with-gaiasky.bat`.
2. Press `Win+R`, type `shell:startup`, and press Enter.
3. Move (or link) the shortcut into the folder that opens.

The agent now starts automatically at login. For a packaged `.exe` build (see GUIDE.md
section 6), point the shortcut at that instead.

### macOS (launchd)

Create `~/Library/LaunchAgents/space.gaiasky.aiagent.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>space.gaiasky.aiagent</string>
    <key>ProgramArguments</key>
    <array>
        <string>/path/to/gaiaskyAIagent/.venv/bin/python</string>
        <string>/path/to/gaiaskyAIagent/run.py</string>
    </array>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><false/>
</dict>
</plist>
```

Then: `launchctl load ~/Library/LaunchAgents/space.gaiasky.aiagent.plist`.

## 4. Roadmap: installing this "like a dataset"

Gaia Sky's dataset manager installs **data** (catalogues, meshes) into its data
directory and registers them in its configuration; it has no mechanism for installing
or launching external **applications**, and extending it to do so would mean modifying
Gaia Sky itself — exactly what this project exists to avoid. So "install like a
dataset" isn't literal, but the spirit of it — a one-step install, no manual editing,
starts automatically — is achievable purely from this side, and is the direction this
project is headed:

- **A real installer** (`pipx install gaiasky-ai-agent`, or a signed installer for
  Windows) that places the packaged binary (see GUIDE.md section 6) in a normal
  application location and offers to register the autostart entries in section 3
  automatically, instead of asking the user to write them by hand.
- **A first-run wizard** that detects Gaia Sky's config file, offers to flip
  `restPort` for the user (with confirmation and a diff shown up front — never
  silently), and detects an existing Ollama install or asks for an API key.
- **A menu-bar/tray icon** so the agent can run permanently in the background and be
  opened with a click, rather than living only in the overlay's own minimize bubble.
- **Signed, notarized packages** for Windows and macOS so installation doesn't require
  a Python toolchain at all.

None of these require, anticipate, or wait on any change to Gaia Sky. They are all
ordinary desktop-application packaging work on this side of the REST boundary, and are
tracked as future work rather than promises attached to a release date.
