# Ultrastealth

Ultrastealth is a standalone Python package for maximum-stealth browser automation.
It uses `rebrowser-playwright` (with CDP leak fixes), real Google Chrome by default, Chromium fallback, and headed Xvfb modes on Linux to avoid bot detection systems.

## Prerequisites
- **Python 3.12+**
- Google Chrome installed for best brand parity, or Chromium/Playwright's bundled Chromium fallback.

## Installation

From a repo clone, installation is one command:

```bash
./install.sh
```

This installs the package in editable mode, installs rebrowser-playwright's
Chromium fallback build, and applies the driver fingerprint patch. To select a specific
Python interpreter:

```bash
PYTHON=/path/to/python3 ./install.sh
```

You can also install directly from GitHub with curl and bash:

```bash
curl -fsSL https://raw.githubusercontent.com/anusoft/ultrastealth/main/install.sh | bash
```

This keeps the checkout at `~/.ultrastealth/src/ultrastealth` by default so the
editable install remains valid. Override with `ULTRASTEALTH_INSTALL_DIR`,
`ULTRASTEALTH_REF`, or `PYTHON` when needed.

If the package is already installed, rerun the same browser install + patch step
directly:

```bash
ultrastealth-install
```

## Basic Usage

You can use the `UltrastealthFetcher` in your own scripts:

```python
import asyncio
from ultrastealth import UltrastealthFetcher

async def fetch_example():
    # headless=False runs headed. On macOS this prefers native headful Google Chrome;
    # on Linux it uses Xvfb when a display is not already available.
    async with UltrastealthFetcher(headless=False) as us:
        # fetch_and_evaluate avoids issues with default Playwright page.content() on SPAs
        title = await us.fetch_and_evaluate(
            url="https://bot.sannysoft.com/",
            js_expression="() => document.title",
            wait_secs=3.0
        )
        print("Page Title:", title)

if __name__ == "__main__":
    asyncio.run(fetch_example())
```

---

## Benchmark

`bot_benchmark.py` scores Ultrastealth against 21 bot-detection / fingerprint sites
(sannysoft, rebrowser, creepjs, deviceandbrowserinfo, iphey, fingerprint-scan,
cloudflare, reCAPTCHA/Turnstile demos, …). Current baseline: **96% (105/109)** —
`docs/research/bot_benchmark_ultrastealth_phase_a_baseline.json`. Scoring counts
`pass`/`fail` only; an untriggered probe (`skip`) and an unreachable site (`error`)
are both reported separately rather than counted as a failure. Run it under a
virtual display:

```bash
# one-time: start Xvfb on :99 (or use your own display)
Xvfb :99 -screen 0 1920x1080x24 &

DISPLAY=:99 python3 bot_benchmark.py                      # all sites
DISPLAY=:99 python3 bot_benchmark.py --sites sannysoft rebrowser
python3 bot_benchmark.py --methods ultrastealth patchright   # compare engines
python3 bot_benchmark.py --compare bot_benchmark_results.json   # reprint table
```

Each site has its own extraction JS + scorer; results are written to JSON and printed
as a table. Notes: `pixelscan`/`incolumitas` only return a verdict from a residential
IP; `cloudflare` (nowsecure.nl) needs the bundled Turnstile solver. The fingerprint
is the consistent, unmodified real-Chrome one — benchmarking showed it beats a
spoofed fingerprint on every fingerprint site tested, so there is no JS-spoofing
layer to opt into.

## Browser Runner Defaults

The default runner is `chrome+default-profile`: Ultrastealth launches Google Chrome with the OS default Chrome user-data directory and `--profile-directory=Default`. On macOS this is:

```text
~/Library/Application Support/Google/Chrome
```

This gives MCP tools access to the same cookies and logged-in state as the normal Chrome default profile. Override it when needed:

```bash
ULTRASTEALTH_RUNNER=chrome+temp-profile ultrastealth-mcp --transport stdio
ULTRASTEALTH_PROFILE_DIRECTORY="Profile 1" ultrastealth-mcp --transport stdio
ULTRASTEALTH_USER_DATA_DIR=/path/to/chrome-user-data ultrastealth-mcp --transport stdio
ultrastealth-mcp --transport stdio --runner chrome+default-profile --user-data-dir "/path/to/chrome-user-data" --profile-directory "Profile 1"
ultrastealth-mcp --transport stdio --runner chromium+default-profile --user-data-dir "/path/to/chromium-user-data" --profile-directory "Profile 1"
```

On Linux the default profile root is:

```text
~/.config/google-chrome
```

If Chrome is already running with the same user-data directory and no profile was explicitly requested, Ultrastealth retries once with a temporary profile so MCP calls can still open the requested page in one shot. If you set `ULTRASTEALTH_PROFILE_DIRECTORY`, `ULTRASTEALTH_USER_DATA_DIR`, or pass profile arguments to an MCP tool, Ultrastealth treats that as an explicit profile request and will not silently fall back to a temporary profile. Close Chrome first if you need automation control of that exact logged-in profile.

MCP calls can request a specific Chrome profile for one-shot navigation:

```text
browser_navigate({"url": "https://mail.google.com", "profile_directory": "Profile 1"})
browser_restart({"navigate_to": "https://mail.google.com", "profile_directory": "Profile 1"})
```

You can also pass `user_data_dir` and `runner` on those MCP calls. Explicit profile requests do not silently fall back to a temporary profile; if Chrome has locked that user-data directory, close Chrome or choose a separate `user_data_dir`.

## Warm Daemon + Fast CLI

For fast, deterministic driving — from the shell, from scripts, or from an agent —
run a **warm-browser daemon** and talk to it with the `ultrastealth` CLI (alias
`us`). The daemon owns **one** persistent-profile Chrome and keeps it warm, so
every command attaches in milliseconds instead of cold-booting Chrome (seconds)
each time. Exactly one process holds the CDP connection; the CLI, the MCP server,
and `connect()` scripts all attach to it — so they share the same session.

```bash
ultrastealth daemon start                                   # warm Chrome, once
ultrastealth browser navigate https://example.com
ultrastealth browser snapshot --interactive --compact      # → [e0] <button> "Login" …
ultrastealth browser click e0 --snapshot-after             # act by ref or CSS selector
ultrastealth browser type e3 --text "user@example.com"
ultrastealth daemon status        # running / socket / pid
ultrastealth daemon stop
```

**Snapshot refs, not screenshots.** `snapshot` returns the accessibility tree with
stable `eN` refs; actions take a ref (`e2`) or a CSS selector. `--snapshot-after`
returns the fresh snapshot in the same response. This is the token/latency win
(the Playwright-MCP model), on real bot-detection-passing Chrome.

**Batch multi-step flows into one call** (also the `browser_batch` MCP tool):

```bash
ultrastealth browser batch - <<'JSON'
[{"op":"navigate","url":"https://example.com/login"},
 {"op":"wait","selector":"#email"},
 {"op":"fill","target":"#email","text":"user@example.com"},
 {"op":"click","target":"e7"},
 {"op":"wait","text":"Welcome"},
 {"op":"snapshot"}]
JSON
```

**From Python** (instant restart, persistent `cf_clearance`):

```python
from ultrastealth import connect
us = connect()                                   # starts the daemon once, then reuses it
await us.call("navigate", url="https://example.com", wait_secs=2.0)
title = (await us.call("get", kind="title"))["title"]
```

Environment: `ULTRASTEALTH_IDLE_TIMEOUT` (seconds the browser stays warm after the
last command; `0` = never close, default `1800`), `ULTRASTEALTH_DAEMON_DIR` (where
the socket/pid/log live; the socket auto-relocates to a short temp path if this
dir would exceed the OS's Unix-socket length limit). Profile selection uses the
same `ULTRASTEALTH_RUNNER` / `_USER_DATA_DIR` / `_PROFILE_DIRECTORY` as the rest of
the stack. The stealth launch path is unchanged — the daemon-driven browser
passes the same bot checks as `UltrastealthFetcher`.

See the bundled `fast-browser` skill (`skills/fast-browser/`) for the agent
playbook and full command reference.

## MCP Server for Claude Code

The ultrastealth MCP server exposes the stealth browser as tools for Claude Code, giving it the ability to navigate, click, type, screenshot, and monitor network traffic — all with maximum anti-detection.

The server runs as an HTTP service (streamable-http transport) on port **8090** by default, managed by PM2.

### Available Tools

**Browser automation:** `browser_navigate`, `browser_snapshot` (stable `eN` refs), `browser_batch` (many steps, one call), `browser_click`, `browser_type`, `browser_get_state`, `browser_screenshot`, `browser_scroll`, `browser_go_back`, `browser_evaluate`, `browser_press_key`, `browser_get_html`, `browser_get`, `browser_is`, `browser_wait`, `browser_hover`, `browser_focus`, `browser_scroll_into_view`, `browser_select_option`, `browser_add_init_script`, `browser_add_script`, `browser_add_style`, `browser_close`

When the warm daemon is running, these MCP tools drive the **same** browser as the `ultrastealth` CLI. Set `ULTRASTEALTH_MCP_NO_DAEMON=1` to make the MCP server own a private browser instead.

**Tab management:** `browser_new_tab`, `browser_list_tabs`, `browser_switch_tab`, `browser_close_tab`

**Network monitoring (DevTools-style):** `browser_network_enable`, `browser_network_disable`, `browser_network_log`, `browser_network_detail`, `browser_network_response_body`, `browser_network_clear`, `browser_network_summary`

**Session, storage, and diagnostics:** `browser_cookies`, `browser_storage`, `browser_state_save`, `browser_state_load`, `browser_console_list`, `browser_console_clear`, `browser_errors_list`, `browser_errors_clear`

**Resource management:** `browser_status`, `browser_cleanup`, `browser_restart`

### Running the Server

```bash
# Start with PM2 (recommended)
pm2 delete ultrastealth-mcp 2>/dev/null
pm2 start /usr/bin/python3 --name "ultrastealth-mcp" \
  --cwd /path/to/your/project -- -m ultrastealth.mcp_server --port 8090

# Or run directly
python3 -m ultrastealth.mcp_server                          # HTTP on 0.0.0.0:8090
python3 -m ultrastealth.mcp_server --port 9000              # HTTP on custom port
python3 -m ultrastealth.mcp_server --transport stdio         # stdio mode (legacy)
```

### Connecting Claude Code

The MCP endpoint is `http://localhost:8090/mcp` (streamable-http).

#### Project-Level (recommended)

Add to `.claude/settings.json` in your project root:

```json
{
  "mcpServers": {
    "ultrastealth": {
      "type": "url",
      "url": "http://localhost:8090/mcp"
    }
  }
}
```

#### User-Level (all projects)

Add to `~/.claude/settings.json`:

```json
{
  "mcpServers": {
    "ultrastealth": {
      "type": "url",
      "url": "http://localhost:8090/mcp"
    }
  }
}
```

#### Via CLI

```bash
claude mcp add --transport http ultrastealth http://localhost:8090/mcp
```

### Verify

After restarting Claude Code, run `/mcp` to confirm the ultrastealth server appears and its tools are listed.

### Example Workflow

```
> use ultrastealth to check bot.sannysoft.com

Claude calls: browser_navigate("https://bot.sannysoft.com")
Claude calls: browser_screenshot()
Claude calls: browser_get_html("table")
→ Returns all test results from the page
```

### Network Monitoring Example

```
> enable network capture and navigate to example.com, then show me all API calls

Claude calls: browser_network_enable()
Claude calls: browser_navigate("https://example.com")
Claude calls: browser_network_log(filter_type="xhr")
→ Returns table of all XHR/fetch requests with status, timing, size
Claude calls: browser_network_detail(request_id=3)
→ Returns full headers and body for a specific request
```

## Driver Fingerprint Patch (`patch_rebrowser.py`)

`rebrowser-playwright`'s bundled Node driver leaks four identifiers that detectors
(e.g. `bot-detector.rebrowser.net`) probe for in the page context:

- `globalThis.__pwInitScripts` — the init-script dedup map, created by the driver
  *before* any bypass runs (so a JS bypass can't reliably hide it).
- `UtilityScript` — the class wrapping every `page.evaluate`; its name leaks into
  `Error().stack` captured by page JS.
- `globalThis.__playwright_builtins__` — a cache of native `setTimeout`/`Date`/`Map`/etc.
  that every injected script recreates before user scripts run, so it can't be hidden
  by a JS bypass either.
- `globalThis.__playwright__binding__` — the CDP `Runtime.addBinding` channel name the
  driver exposes on every page regardless of whether the caller uses `exposeBinding`;
  a JS bypass races the CDP call and can only win after the property already existed.

`patch_rebrowser.py` renames all four at the driver source (`__pwInitScripts → __execGuards`,
`UtilityScript → ExecutionProxy`, `__playwright_builtins__ → __nativeRefs`,
`__playwright__binding__ → __execChannel`), consistently so functionality is preserved.
With all four renames applied, `bot-detector.rebrowser.net` scores **6/6 pass, 0 fail**
(4 of its 10 probes need main-world access that `alwaysIsolated` mode denies by design —
see below — and are correctly reported as `skip`, not a failure). Full 21-site benchmark:
**96% (105/109)** — `docs/research/bot_benchmark_ultrastealth_phase_a_baseline.json`.

```bash
ultrastealth-install --skip-browser-install        # apply after dependency upgrades
ultrastealth-patch --check                         # report status
ultrastealth-patch --revert                        # undo
```

The patch edits the *installed* pip package, so **a `pip install -U rebrowser-playwright`
reverts it — re-run `ultrastealth-install --skip-browser-install` afterward.**
It is idempotent, revertible, and
upstream-safe: each edit anchors on the original token and *warns + skips* (never
corrupts) if upstream changed it. `UltrastealthFetcher.start()` also attempts to
apply the patch before launching Chrome/Chromium and logs a warning if it cannot.

### Maximum stealth: isolated evaluate (opt-in tradeoff)

The one remaining rebrowser detection — `mainWorldExecution` — is *by design*:
rebrowser's default `addBinding` mode runs `page.evaluate` in the **main world**
(detectable). Setting:

```bash
export REBROWSER_PATCHES_RUNTIME_FIX_MODE=alwaysIsolated
```

runs `evaluate` in an **isolated world** → **0 positive detections** on rebrowser.
Trade-off: isolated `evaluate` can read the shared **DOM** (`querySelector`,
`outerHTML`, embedded JSON like `__NEXT_DATA__` via `textContent`) but **not**
main-world JS globals (`window.someAppState`). Ultrastealth sets this mode by
default before launch; set `REBROWSER_PATCHES_RUNTIME_FIX_MODE=addBinding` if you
need main-world JS access.

## Alternative Engine: patchright (opt-in)

`UltrastealthFetcher(engine="patchright")` / `ULTRASTEALTH_ENGINE=patchright` switches
the automation driver from `rebrowser-playwright` to `patchright`, an independently
maintained undetected-Playwright fork. Head-to-head across all 21 benchmark sites it
scores identically to the default engine (96%, same failures) but runs ~22% faster
overall — most of that on slow challenge-solve sites (`cloudflare`, `egp_announcements`).

**It is not a drop-in replacement for the default.** Patchright's `Page` has no
`accessibility` attribute — upstream Playwright removed it by the version patchright
tracks — so it cannot back the `browser_snapshot`/`browser_batch` `eN` ref system this
project's MCP server and CLI depend on. Use it only through `UltrastealthFetcher.fetch()`
/ `fetch_and_evaluate()`, or `bot_benchmark.py --methods patchright`. Full verified
findings: `CLAUDE.md`.

## Browser Ops

Beyond the CLI/MCP tools listed above, `browser_core.py`'s op registry (reachable via
`ultrastealth browser <op>`, the Python `client.connect()` API, and `browser_batch`
steps) also includes:

- `cookies` — the current browser context's cookies.
- `find` — best-matching interactive element `eN` ref for a natural-language-ish
  query, scored against the accessibility snapshot's roles/names (no LLM call).
