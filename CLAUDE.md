# Ultrastealth MCP Handoff

## Current Work

- MCP profile selection is implemented on `browser_navigate` and `browser_restart`.
- Both tools accept `profile_directory`, `user_data_dir`, and `runner`.
- When a requested profile differs from the active browser profile, MCP restarts the browser with the requested profile.
- Env or tool profile requests disable temporary-profile fallback so the tool does not silently use the wrong Chrome/Chromium profile.
- Default-profile launch still retries once with a temporary profile when no explicit profile is requested, preserving one-shot navigation when normal Chrome locks the default profile.

## Warm Daemon + Fast CLI

- A standalone daemon (`ultrastealth daemon start`) owns **one** warm, persistent-profile Chrome; the `ultrastealth`/`us` CLI, the MCP server, and `connect()` scripts all attach to it over a Unix socket. Only the daemon holds the CDP connection (no multi-connection churn).
- Shared engine `browser_core.py` is the single implementation of every op (navigate/snapshot/click/type/wait/get/is/evaluate/batch/…). `daemon.py` serves it as JSON-RPC; `client.py` is the client + `connect()`; `cli.py` is the CLI; the MCP server gained `browser_batch` + `browser_snapshot` and **auto-routes to the daemon when its socket exists** (opt out with `ULTRASTEALTH_MCP_NO_DAEMON=1`).
- Speed levers: warm reuse (no cold start), stable `eN` snapshot refs + `--snapshot-after`, and `browser_batch` (N steps → 1 call). The stealth launch path in `fetcher.py` is unchanged for the daemon, so bot-detection parity is preserved. (Note: the JS `bypasses/*.js` chain this line used to reference no longer exists — see "Benchmark & Stealth Posture" below.)
- Socket/pid/log live under `ULTRASTEALTH_DAEMON_DIR` (default `~/.ultrastealth`); the socket auto-relocates to a short temp path if that dir would exceed the AF_UNIX length limit. `ULTRASTEALTH_IDLE_TIMEOUT` controls keep-warm (`0` = never close).
- Agent playbook + full command list: the bundled `fast-browser` skill (`skills/fast-browser/`).

## Generated Crawler Scripts

- Put generated website crawler entrypoints in `out/<web>/<web>.mjs`, for example `out/powerbuy/powerbuy.mjs` or `out/watsons/watsons.mjs`.
- Keep generated crawler plans, smoke outputs, and sample JSON under `out/` as local artifacts.
- Do not include `out/` generated artifacts in GitHub commits or pushes unless the user explicitly asks for a generated artifact to be versioned.
- When committing unrelated source/docs/skill changes, stage paths explicitly and avoid `git add out/` or broad `git add .`.
- Generated crawler help text should show the fresh-machine bootstrap:
  `curl -fsSL https://raw.githubusercontent.com/anusoft/ultrastealth/main/install.sh | bash`
  and
  `curl -fsSL https://raw.githubusercontent.com/anusoft/scrapling-js/main/install.sh | bash`.

## Restart Test

For exact-profile tests, prefer Google Chrome for best userAgentData brand parity. Chromium remains available by setting `--runner chromium+default-profile`.

Before restarting MCP for an exact-profile test, close regular Chrome so the shared Chrome user-data dir is not locked. Then start Codex/MCP with:

```toml
[mcp_servers.ultrastealth]
command = "ultrastealth-mcp"
args = ["--transport", "stdio", "--user-data-dir", "/Users/mac/Library/Application Support/Google/Chrome", "--profile-directory", "Profile 1"]
```

After restarting the MCP server, test one-shot navigation:

```json
{"url":"https://www.google.com"}
```

Test a specific Chrome profile:

```json
{"url":"https://mail.google.com","profile_directory":"Profile 1"}
```

If Chrome/Chromium has locked the requested profile, close that browser or pass a separate `user_data_dir`:

```json
{"url":"https://mail.google.com","user_data_dir":"/path/to/Chrome/User Data","profile_directory":"Profile 1"}
```

## Benchmark & Stealth Posture (2026-07-30)

A full prior-art survey and same-day execution pass landed. Full detail, evidence, and
before/after numbers: `docs/plan/prior/07-roadmap.md` (start there — each phase has an
inline "✅ DONE, measured" callout). Summary:

- **Benchmark instrument fixed.** `bot_benchmark.py` used to fold untriggered probes
  (`skip`) and site outages (5xx) into the fail count, and had a substring matcher that
  inverted intent on 3 `infosimples` tests. Corrected baseline: **96% (105/109)** across
  21 sites — `docs/research/bot_benchmark_ultrastealth_phase_a_baseline.json`. The one
  remaining genuine failure at `fingerprintscan` sits exactly on its pass/fail boundary
  (`bot_risk_score = 50`, rule is `< 50`).
- **The `bypasses/*.js` JS-fingerprint-spoofing chain (13 files) was deleted entirely.**
  It was gated off by default already — this project's own benchmark evidence showed the
  clean, unmodified real-Chrome fingerprint beat the spoofed one on every fingerprint
  site tested — and the dead code carried several real bugs nobody hit (self-inconsistent
  worker language arrays, an instance-level `defineProperty` leak, a `ReferenceError` from
  an undefined helper). If a spoofed-persona system is ever built again, do it as
  internally-consistent generated personas (à la `apify/fingerprint-suite`'s model), not
  hand-written per-signal constants — see `docs/plan/prior/04-fingerprint-layer.md`.
- **`mcp[cli]` upgraded 1.28.0 → 2.0.0.** `FastMCP` → `MCPServer` (rename + protocol
  rewrite); `mcp.settings.host/port` no longer exist — host/port now pass directly to
  `mcp.run(transport=..., host=..., port=...)`. Verified against both stdio and
  streamable-http transports with a real MCP `initialize` handshake, not just unit tests.
- **New `browser_core.py` ops**: `cookies` (context cookie jar) and `find` (natural-
  language-ish query → best-matching `eN` ref, via `difflib` text/role/name scoring, no
  LLM call). Both reachable via `ultrastealth browser <op>`, `client.connect()`, and
  `browser_batch` steps — no dedicated MCP tool, following the precedent of other
  passthrough ops (`get`/`is`/`wait`).
- **Per-session op lock** (`browser_core.get_op_lock(session=None)`) replaces the old
  module-global lock — externally identical today (one session), but removes the
  structural blocker to future multi-session support.
- **Health model improved**: `browser_core.health_check()` checks OS-level process
  liveness (`psutil`, same heuristic `mcp_server._hard_kill_browser` already used)
  independently of CDP responsiveness, distinguishing `healthy`/`unresponsive`/
  `process_exited`. `daemon._health_watchdog` calls this instead of a bare `page.title()`
  ping, and deliberately catches *any* unexpected exception per-tick (not just the
  expected `BrowserCoreError`) — a first-pass version narrowed this to only
  `BrowserCoreError`, which meant one unexpected exception would permanently kill the
  watchdog task for the daemon's remaining lifetime (it's a fire-and-forget
  `asyncio.create_task()`, never restarted). Don't re-narrow this without re-checking
  that failure mode.
- **Deferred, on purpose** (not attempted): generated fingerprint personas, full
  multi-session daemon support (context/profile/proxy per session, LRU/TTL eviction), a
  `capture` op (screenshot + snapshot from the same DOM epoch). All flagged in the roadmap
  as genuinely larger `M`/`L` architecture work.

## Patchright Engine (opt-in)

`UltrastealthFetcher` supports an alternative automation engine via
`engine="patchright"` / `ULTRASTEALTH_ENGINE=patchright` (see `ENGINE_*` in
`fetcher.py`). Default remains `rebrowser`; existing behavior is unchanged
unless a caller opts in. Verified findings (2026-07-30, head-to-head against
`rebrowser_playwright`; full research in `docs/plan/prior/02-playwright-patching.md`):

- **Blocking: `page.accessibility.snapshot()` does not exist on patchright's
  `Page`** (upstream Playwright removed it by the version patchright tracks;
  `'accessibility' in dir(patchright.async_api.Page)` is `False`).
  `browser_core.py`'s entire eN snapshot/ref system depends on that call, so
  the patchright engine must **not** be wired into `browser_core.py` / MCP
  `browser_snapshot` — only `UltrastealthFetcher.fetch()`/`fetch_and_evaluate()`
  and `bot_benchmark.py`'s `patchright` method use this engine today, and
  neither calls `page.accessibility`.
- The Console domain (`page.on("console")`) is disabled unconditionally under
  patchright. Nothing in this codebase's own automation path currently relies
  on console events, so this is a known limitation, not a regression.
- `add_init_script` reaches the page via the standard CDP
  `Page.addScriptToEvaluateOnNewDocument` path on **both** http(s) and
  `file://` pages under patchright (verified directly against a local
  `file://` fixture with `isolated_context=False`). Patchright's *additional*
  HTML/CSP-rewrite injection route is http(s)-only; it supplements, it does
  not replace, CDP-based delivery.
- `patch_rebrowser.py` is gated behind `self.engine == ENGINE_REBROWSER` in
  `fetcher.py`'s `start()` and is never invoked for the patchright engine (it
  targets `rebrowser_playwright`'s on-disk driver layout, which patchright's
  single-file `coreBundle.js` driver doesn't have).

## Verification

```bash
/Users/mac/.local/pipx/venvs/ultrastealth/bin/python -m unittest discover -s tests
```
