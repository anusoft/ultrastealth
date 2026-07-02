# Ultrastealth Warm-Daemon Fast Driver — Design

**Date:** 2026-07-02
**Status:** Approved (design), pending implementation plan
**Author:** Claude + anu

## Goal

Make the whole Ultrastealth stack fast by eliminating the two biggest costs in
browser automation: **cold Chrome starts** and **per-action LLM round-trips**.
Deliver a **standalone warm-browser daemon** that owns one persistent-profile
real Chrome, plus a **cmux-style CLI**, a **`browser_batch` MCP tool**, a
**snapshot-ref model**, and **skills** that teach agents and scripts to use the
fast path. Claude (via MCP), the shell/CLI, and reusable scraper scripts all
drive the **same warm browser**.

## Non-Goals

- Multi-browser pools / horizontal scaling. One warm browser instance for now.
  (The daemon boundary leaves room to add pooling later; not built now.)
- Changing the stealth/bypass model. The rebrowser patch, JS bypasses, and
  `chrome+default-profile` defaults are preserved unchanged. Stealth parity is a
  release gate, not a feature to modify.
- Remote multi-tenant/authenticated access. Local Unix socket first; optional
  loopback TCP only. No network auth layer.
- Windows service integration. macOS and Linux first (matches current support).

## Background (current state)

- `UltrastealthFetcher` (`fetcher.py`) cold-launches a persistent-context real
  Chrome on every `.start()` (~seconds). It already supports an opt-in CDP
  endpoint via `ULTRASTEALTH_CDP_PORT`.
- The MCP server (`mcp_server.py`) already holds **one warm browser as a module
  global** (`_fetcher`/`_page`) across ~45 `browser_*` tools, with network and
  diagnostic capture, wedge detection (`_browser_wedged`), and an interactive
  accessibility snapshot (`_get_interactive_elements` via
  `page.accessibility.snapshot()`). It runs on HTTP:8090 (PM2) or stdio.
- There is **no CLI** to drive the browser and **no batch tool**, so every agent
  action is its own LLM turn and every reusable script cold-starts its own Chrome.

## Prior art that shaped this (research)

- **Playwright MCP** — operates on the accessibility tree; every interactive
  element gets a **stable `ref` (`e5`)**; most tools **auto-return a fresh
  snapshot after each action**; screenshots/vision are opt-in (~5% of cases).
  → adopt stable refs, `--snapshot-after`, snapshots over screenshots.
- **chrome-devtools-mcp** — multiple processes each opening their own CDP
  connection causes reconnect churn, `Network.enable` timeouts, and repeated
  approval prompts (issues #1094, #1763). → **exactly one process owns the CDP
  connection** (the daemon); all other clients talk to the daemon.
- **browser-use** — the big latency win is **reusing the CDP session across
  steps** (PR #3861) rather than reconnecting per action; `keep_alive=True` and
  `user_data_dir + profile_directory` for persistent auth. → keep warm, reuse
  the connection, persistent profile.
- **Browserless** — warm pool + idle-timeout with reconnect resetting the timer
  + health checks that detect an unhealthy browser even when the process is
  alive. → idle-timeout keep-warm, health-check auto-restart.

## Architecture

```
                         ┌───────────────────────────────────────┐
   ultrastealth CLI ───► │  ultrastealth daemon (single process)  │
   (shell / scripts)     │  ├─ owns ONE warm Chrome                │──CDP──► real Chrome
                         │  │   (persistent profile, kept warm)    │        (default profile;
   MCP server ─────────► │  ├─ browser_core  (shared logic)        │         cookies / cf_clearance
   (Claude tools)        │  ├─ per-tab snapshot ref-map            │         persist across restarts)
                         │  ├─ batch executor                      │
   emitted scraper ────► │  └─ control API (Unix socket + TCP opt) │
   (connect() helper)    └───────────────────────────────────────┘
                          idle-timeout keep-warm · health-check auto-restart
```

**One CDP owner, one warm browser, three front-ends** (control socket, MCP
tools, CLI). Cold start is paid once — or never, when the daemon is already up.

## Components and interfaces

### 1. `browser_core` (new module, extracted from `mcp_server.py`)

The browser-owning logic factored out into plain async functions that operate on
the singleton and return **structured, JSON-serializable results** (dicts), not
LLM-formatted strings. This is the single implementation every front-end shares.

- Owns: `_fetcher`, `_page`, browser config, per-tab ref-map, network log,
  diagnostic (console/error) log.
- Provides: `ensure_browser`, `hard_kill_browser`, `navigate`, `snapshot`
  (a11y tree → stable refs), `click`, `type`, `fill`, `press`, `hover`,
  `select`, `scroll`, `scroll_into_view`, `get`, `is_`, `find`, `wait`,
  `evaluate`, `add_init_script`, `screenshot`, `cookies`, `storage`,
  `state_save/load`, `tabs_*`, `network_*`, `console_*`, `errors_*`, `status`,
  `close`, and `batch`.
- Resolution: refs resolve **in-process** (the daemon owns both the page and the
  ref-map). Each ref stores a Playwright element handle plus a generated
  fallback selector. Stale ref (DOM changed) → typed error `stale_ref` telling
  the caller to re-snapshot (matches Playwright MCP semantics).
- Concurrency: an async lock **per tab** serializes operations on a page.

**Contract:** `browser_core` is behavior-preserving w.r.t. today's MCP tools.
MCP tool functions become thin wrappers: call core → format the string the LLM
sees. Existing MCP tests must stay green.

### 2. `daemon` (new module)

A long-running asyncio process that owns `browser_core` and serves a control API.

- **Transport:** newline-delimited JSON-RPC over a **Unix domain socket**
  (default `~/.ultrastealth/daemon.sock`, perms `0600`). Optional loopback TCP
  via `--tcp 127.0.0.1:PORT` (opt-in).
  - Request: `{"id": N, "cmd": "click", "args": {...}}`
  - Response: `{"id": N, "ok": true, "result": {...}}`
    or `{"id": N, "ok": false, "error": {"type": "...", "message": "..."}}`
- **Lifecycle files** in `~/.ultrastealth/`: `daemon.sock`, `daemon.pid`,
  `daemon.log`. `start` (background/daemonize), `stop` (graceful: close browser,
  remove sock/pid), `status` (uptime, profile, warm?, tab count), `logs` (tail).
  `start` cleans a stale sock/pid if the recorded PID is dead.
- **Keep-warm:** `--idle-timeout SECS` (default `1800`, `0` = never). Each
  request resets the idle timer. On expiry the daemon **closes the browser but
  keeps listening**; the next request relaunches. Frees RAM without losing the
  attach-fast property.
- **Health:** a watchdog pings `page.title()` under a timeout; on wedge it
  hard-kills and relaunches (reusing the existing `_browser_wedged` path).
- **Profile:** default `chrome+default-profile`; honors
  `ULTRASTEALTH_RUNNER` / `_PROFILE_DIRECTORY` / `_USER_DATA_DIR` and the same
  CLI flags the MCP server accepts, so persistent auth carries across restarts.
- **Single owner:** if a second daemon tries to bind an in-use profile/sock it
  fails with a clear message rather than opening a competing CDP connection.

### 3. `client` (new module)

`UltrastealthClient` — connects to the socket, sends commands, returns results.

- Auto-starts the daemon if the sock is absent (spawn `ultrastealth daemon start`,
  wait for readiness), unless `--no-autostart`.
- `connect(**profile) -> UltrastealthClient` convenience for scripts and the
  MCP-as-client path. Exposes method mirrors (`navigate`, `wait`, `click`,
  `evaluate`, `snapshot`, …) that map 1:1 to core ops.

### 4. Snapshot ref model

- `snapshot(interactive=True, compact=True, diff=False)` walks the a11y tree
  (existing `_get_interactive_elements`), assigns **stable `e1/e2` refs** for the
  tab, and stores the ref-map. `compact` trims to role + name + ref; `diff`
  returns only what changed since the tab's previous snapshot.
- Actions accept **a ref or a CSS selector** (`click e2` or `click "#submit"`).
- `--snapshot-after` on any mutating action returns the fresh snapshot in the
  same response — one round-trip instead of act-then-observe.
- Screenshots remain available but opt-in (Playwright vision-mode analogue).

### 5. `browser_batch`

Execute a list of steps in one call, under the tab lock:

```json
[{"op":"navigate","url":"…"},{"op":"wait","selector":"…"},
 {"op":"click","ref":"e2"},{"op":"type","ref":"e5","text":"…"},{"op":"snapshot"}]
```

Returns `[{ok,result}, …]`; `stop_on_error` (default true) halts on first
failure. Exposed as an MCP tool (`browser_batch`) and CLI (`us browser batch`).
Collapses N LLM turns into 1.

### 6. CLI (`cli.py`) — cmux-compatible surface

Entry points: `ultrastealth` and alias `us` (both → `cli.main`).

- `daemon start|stop|status|logs`
- `browser navigate|goto|back|forward|reload|url|title`
- `browser wait --selector|--text|--url-contains|--load-state|--function --timeout-ms`
- `browser snapshot [--interactive] [--compact] [--diff]` · `screenshot --out`
- `browser get <text|html|url|title|attr>` · `is <visible|enabled|checked> <ref|sel>` · `find <text>`
- `browser click|dblclick|hover|focus|check|uncheck|scroll-into-view <ref|sel>`
- `browser type <ref|sel> --text` · `fill <ref|sel> --text` · `press <key>` · `select <ref|sel> --value`
- `browser scroll [--direction --amount]` · `eval <js>` · `add-init-script|add-script|add-style`
- `browser cookies|storage|state save/load` · `tab list|new|switch|close`
- `browser console list|clear` · `errors list|clear` · `network enable|log|detail|body|summary|clear`
- `browser batch <file.json|->`
- Global flags: `--snapshot-after`, `--json`, `--tab <id>`, `--socket <path>`,
  `--timeout <ms>`, `--no-autostart`.

Default output is human-readable; `--json` emits raw structured results for
scripting. The command names deliberately mirror the `cmux-browser` skill so its
mental model transfers 1:1.

### 7. MCP as daemon-client

`mcp_server`'s `ensure_browser` detects a running daemon (sock present) and
routes core calls **through the client**, so MCP and CLI share one warm browser.
With no daemon it falls back to owning its own browser (today's behavior),
keeping full back-compat. Adds the `browser_batch` tool and stable-ref snapshot
output.

### 8. Skills

- **New `fast-browser` skill** — the `cmux-browser` skill re-pointed at the
  Ultrastealth CLI: ensure the daemon is warm, drive with snapshot refs +
  `--snapshot-after` + `batch` instead of one action per turn, screenshots only
  for human review. Includes a `references/commands.md` command map.
- **Update `craft-scraper`** — Path B emitted scripts use `connect()` to attach
  to the warm daemon (instant restart, persistent `cf_clearance`) for the common
  navigate→wait→evaluate flow, instead of cold-launching `UltrastealthFetcher`.
  The cold-launch `UltrastealthFetcher` path stays documented for flows that need
  a raw Playwright `page_action` (interactive multi-step). The discovery loop
  uses batch + compact snapshots.

## Data flow (example: agent fills a login form)

1. Agent calls `browser_batch` with `[navigate, wait #email, fill e-ref pw,
   click submit, wait "Welcome", snapshot]`.
2. MCP routes the batch to the daemon over the socket (one message).
3. Daemon runs the steps on the warm page under the tab lock, refreshing the
   ref-map at the final `snapshot`.
4. One response returns all step results + the fresh compact snapshot.
   → one LLM round-trip, zero cold start.

## Error handling

- **Daemon down:** client auto-starts it (default) or errors with a
  `daemon start` hint (`--no-autostart`).
- **Browser wedged:** watchdog hard-kills + relaunches; the in-flight command
  returns a typed `wedged` error and the next call is clean.
- **Stale ref:** typed `stale_ref` error → "re-snapshot" (page changed since the
  ref-map was built).
- **Profile locked** (Chrome already open on that user-data-dir): the existing
  explicit-request messaging applies; the daemon reports it rather than silently
  using a temp profile when a profile was explicitly requested.
- **Stale sock/pid:** `daemon start` removes them if the recorded PID is dead.

## Security

- Unix socket at `0600`, owner-only. TCP is loopback-only and opt-in. No remote
  auth layer in scope; document that TCP exposes full browser control locally.

## Phasing (each phase independently shippable + tested)

- **Phase 0 — Extract `browser_core`.** Behavior-preserving refactor; MCP tools
  call core; full existing suite green.
- **Phase 1 — Daemon + client.** Control socket, lifecycle, keep-warm, health.
- **Phase 2 — CLI.** cmux-parity surface, snapshot refs, `--snapshot-after`, batch.
- **Phase 3 — MCP.** `browser_batch` tool + stable-ref snapshot + flip MCP to
  daemon-client (shared browser), back-compatible.
- **Phase 4 — Skills.** New `fast-browser`; update `craft-scraper` fast path.
- **Phase 5 — Verify + docs.** Full unit suite, cold-vs-warm latency benchmark,
  bot-benchmark stealth-parity check, README/CLAUDE.md updates.

## Testing strategy

Follows the existing `tests/` style (fake page/context, `unittest`):

- Socket JSON-RPC round-trip against a fake core.
- Ref-map assignment + snapshot `--diff`; `stale_ref` behavior.
- Batch executor: sequencing, `stop_on_error`, final snapshot.
- CLI argument parsing → correct core op + args (no live browser).
- MCP-as-daemon-client routing, plus MCP-owns-browser fallback (existing tests).
- Daemon lifecycle: start/stop, stale-sock cleanup, idle-timeout browser close.
- **Latency benchmark:** cold-start `UltrastealthFetcher` vs warm-attach via the
  daemon, printed as a before/after table (proves the speedup).
- **Stealth parity:** `bot_benchmark.py --sites sannysoft rebrowser` unchanged.

Verification command (per CLAUDE.md):
`/Users/mac/.local/pipx/venvs/ultrastealth/bin/python -m unittest discover -s tests`

## Risks and mitigations

- **Refactor regressions** → Phase 0 is behavior-preserving and gated by the
  existing suite before anything new is built.
- **Stale refs across DOM changes** → explicit `stale_ref` error + re-snapshot,
  the Playwright MCP contract.
- **Daemon orphans / stale sock** → PID liveness check + cleanup on start.
- **Two CDP owners** → forbidden by design; only the daemon connects to Chrome.
- **Multi-tab races** → per-tab async lock in `browser_core`.

## Success criteria

1. `ultrastealth daemon start` then repeated `us browser …` commands run with
   **no per-command Chrome cold start** (measured warm-attach ≪ cold-start).
2. A multi-step flow runs as **one** `browser_batch` MCP call.
3. MCP and CLI drive the **same** warm browser (shared cookies/session).
4. A craft-scraper Path B script attaches to the warm daemon and reruns without
   re-launching Chrome.
5. Stealth parity: sannysoft/rebrowser scores unchanged vs the committed
   baseline.
6. Full unit suite green.
