---
name: fast-browser
description: Drive the Ultrastealth warm-browser daemon fast from the shell or as an agent. Use for stealth browser automation where speed and low token cost matter — snapshot refs, --snapshot-after, batched multi-step flows, and a persistent warm profile that avoids cold Chrome starts. Triggers on "drive the browser fast", "use the warm browser", "automate this page with ultrastealth", "click/type/snapshot via CLI", "keep the browser warm".
allowed-tools: Bash, Read, Write, Edit, mcp__ultrastealth__browser_batch, mcp__ultrastealth__browser_snapshot, mcp__ultrastealth__browser_navigate, mcp__ultrastealth__browser_click, mcp__ultrastealth__browser_type, mcp__ultrastealth__browser_get_state
---

# Fast Browser

Drive one **always-warm** stealth Chrome owned by the `ultrastealth` daemon. The
browser starts once and stays warm; every command attaches in milliseconds. This
is the Ultrastealth analogue of the cmux-browser skill — same mental model
(navigate → snapshot → act → re-snapshot), but on real, bot-detection-passing
Chrome with a persistent profile.

> Prerequisite: the `ultrastealth` CLI is installed (`ultrastealth --help`) and,
> for the MCP path, the Ultrastealth MCP is connected. The daemon auto-starts on
> first use.

## Golden rules (these ARE the speed wins)

1. **Keep it warm.** `ultrastealth daemon start` once. Every later command — CLI,
   MCP tool, or a `connect()` script — reuses the same browser: no cold Chrome
   start, shared cookies/session/`cf_clearance`.
2. **Snapshot refs, not screenshots.** `ultrastealth browser snapshot
   --interactive --compact` returns stable `eN` refs; act with `click e2`,
   `type e5 --text "…"`. Re-snapshot after navigation or a DOM change.
   Screenshots are for human review only (like Playwright's opt-in vision mode).
3. **Batch multi-step flows.** Collapse `navigate → wait → click → type →
   snapshot` into ONE `browser_batch` (MCP) / `ultrastealth browser batch`
   (CLI) call instead of one action per turn.
4. **`--snapshot-after`** on a mutating action returns the fresh snapshot in the
   same response — no separate observe step.

## Core loop (CLI, deterministic + fast)

```bash
ultrastealth daemon start
ultrastealth browser navigate https://example.com
ultrastealth browser snapshot --interactive --compact   # → [e0] <button> "Login" …
ultrastealth browser click e0 --snapshot-after
ultrastealth browser type e3 --text "user@example.com"
ultrastealth browser get title
```

Add `--json` for machine-readable output; `--tab`/`--socket` to target a
specific daemon; `--no-autostart` to fail instead of starting one.

## Core loop (agent via MCP)

Prefer `browser_batch` with a JSON step list and end with a `snapshot` step so you
get refs back in one call. Use `browser_snapshot` to refresh refs; use
`browser_navigate`/`browser_click`/`browser_type` for single actions. When a
daemon is running, these MCP tools and the CLI drive the **same** warm browser.

## Batch example (one round-trip)

```bash
ultrastealth browser batch - <<'JSON'
[{"op":"navigate","url":"https://example.com/login"},
 {"op":"wait","selector":"#email"},
 {"op":"fill","target":"#email","text":"user@example.com"},
 {"op":"fill","target":"#password","text":"secret"},
 {"op":"click","target":"e7"},
 {"op":"wait","text":"Welcome"},
 {"op":"snapshot"}]
JSON
```

The same array works as the `steps` argument to the `browser_batch` MCP tool.

## From a Python script (instant restart)

```python
from ultrastealth import connect
us = connect()                                   # starts daemon once, then reuses
await us.call("navigate", url="https://example.com", wait_secs=2.0)
await us.call("wait", selector="[data-product]", timeout_ms=15000)
rows = (await us.call("evaluate", javascript=EXTRACT))["result"]
```

## Stale refs

Refs are stable only within a snapshot. After navigation or a DOM change, a stale
ref returns a `stale_ref` error — just re-`snapshot` and use the new refs.

## When NOT to use the daemon

For a one-off script that needs a raw Playwright `page` object with a custom
`page_action` (complex interaction the RPC ops can't express), use
`UltrastealthFetcher` directly — see the craft-scraper skill, Path B. Everything
else should attach to the warm daemon.

See [references/commands.md](references/commands.md) for the full command list.
