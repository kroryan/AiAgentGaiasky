# Proposal: one-click discovery and install of external companion tools

**Audience:** Gaia Sky maintainers. This document proposes an optional, minimal hook
Gaia Sky itself could add to make external companion tools (this AI agent being the
motivating example) installable and launchable the way a dataset is — without Gaia Sky
adopting, bundling, or depending on any specific tool, and without any of the scope
this project was originally turned away for (no in-process agent, no bundled LLM
harness, no new maintenance burden on the core application).

**Status:** proposal / RFC. Nothing in this repository depends on any part of this
document; the AI agent already works standalone against the existing REST API, exactly
as requested. This is a separate, optional suggestion for a follow-up improvement to
Gaia Sky's own UX, offered because building this agent surfaced a genuine gap: Gaia
Sky's dataset manager gives users a one-click experience for adding data, but there is
no equivalent for the small ecosystem of scripts and external tools that already use
the REST/scripting API (this agent, and presumably others written by other users).

## The gap

Today, using any external REST API tool with Gaia Sky requires the user to:

1. Manually locate and edit `config.yaml` to set `program.net.restPort`.
2. Manually install and start the external tool.
3. Manually know it exists in the first place — there is no discovery mechanism.

This is fine for a developer, and it is what this project's own documentation walks
through (see [INSTALL.md](INSTALL.md)). It is not comparable to the one-click
experience Gaia Sky already gives users for datasets, which is the bar users
reasonably expect for anything advertised as "just works."

## What is *not* being proposed

To be unambiguous about scope, given the history here:

- **Not** bundling this or any other agent inside Gaia Sky.
- **Not** adding a plugin runtime, sandboxed execution environment, or process
  supervisor to Gaia Sky. Gaia Sky would not launch, monitor, or manage the external
  tool's process.
- **Not** any network call Gaia Sky makes on the user's behalf to fetch or run
  third-party code automatically. Any install step remains an explicit user action.
- **Not** a new maintenance surface tied to any one external tool's release cycle.

## What could actually help: a `restApiClients` registry, nothing more

A single, static, opt-in section in `config.yaml` (or a small sibling file, e.g.
`rest-clients.json`, in the config directory) listing external tools the user has
chosen to register — populated once, by hand or by that tool's own installer, never by
Gaia Sky reaching out to the network:

```yaml
# Purely informational to Gaia Sky; it never launches or manages these.
restApiClients:
  - name: "Gaia Sky AI Agent"
    description: "External AI assistant, driven over this REST API."
    url: "https://github.com/<org>/gaiaskyAIagent"
    # Optional: a local command the UI can offer to run, same as double-clicking
    # a shortcut. Gaia Sky treats this as an opaque string; it does not manage
    # the resulting process, only starts it, exactly like a user double-click would.
    launchCommand: "gaiasky-ai-agent"
```

With that, the only Gaia Sky-side change is cosmetic and entirely optional:

1. **A "Connected tools" panel**, next to the existing dataset manager, that lists
   whatever is in `restApiClients` and shows whether `program.net.restPort` is
   currently enabled (a config read, nothing more).
2. **A "Launch" button** per entry that runs `launchCommand` as an ordinary child
   process (`ProcessBuilder`/`Runtime.exec`, fire-and-forget — the same primitive
   already used to open a file browser or a URL in the system browser) if present.
3. **A one-line toggle** for `restPort` itself, so enabling the REST API is a checkbox
   instead of a `config.yaml` edit — useful on its own, independent of the rest of this
   proposal, and the single highest-value, lowest-risk change here.

That is the entire ask. No sandboxing, no auto-update, no telemetry, no code execution
Gaia Sky wasn't already capable of via a basic "open this path" action.

## How an installer would populate this, from the tool's side

Entirely on the tool's own side, matching how desktop installers already register
autostart entries (see [INSTALL.md](INSTALL.md) section 3): this agent's installer
would, with the user's confirmation, append its own entry to `restApiClients` and flip
`restPort` if the user agrees — the same file, opened and rewritten the same way this
project's own diagnostics already read it, just with a written section instead of a
read one. Gaia Sky's role stays passive: display what's registered, offer the REST
toggle, offer to run a listed command. It never fetches, verifies, or vouches for what
an entry points to; that trust decision stays the user's, exactly as it is when they
run any other installer today.

## Why this is worth doing regardless of any one tool

The REST/scripting API is a public, documented surface that already invites external
tools — this project is simply the first to need a real installer story. A registry
and a toggle cost little and generically improve the experience for every current and
future external tool built the same way, without Gaia Sky ever needing to know what
any of them do.
