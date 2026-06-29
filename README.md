# Ultrastealth

Ultrastealth is a standalone Python package for maximum-stealth browser automation.
It uses `rebrowser-playwright` (with CDP leak fixes), real Google Chrome by default, Chromium fallback, headed Xvfb modes on Linux, and several advanced JS bypasses to avoid bot detection systems.

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

`bot_benchmark.py` scores Ultrastealth against ~15 bot-detection / fingerprint sites
(sannysoft, rebrowser, creepjs, deviceandbrowserinfo, iphey, fingerprint-scan,
cloudflare, …). Run it under a virtual display:

```bash
# one-time: start Xvfb on :99 (or use your own display)
Xvfb :99 -screen 0 1920x1080x24 &

DISPLAY=:99 python3 bot_benchmark.py                      # all sites
DISPLAY=:99 python3 bot_benchmark.py --sites sannysoft rebrowser
python3 bot_benchmark.py --compare bot_benchmark_results.json   # reprint table
```

Each site has its own extraction JS + scorer; results are written to JSON and printed
as a table. Notes: `pixelscan`/`incolumitas` only return a verdict from a residential
IP; `cloudflare` (nowsecure.nl) needs the bundled Turnstile solver. The default
fingerprint is intentionally the consistent real-Chrome one — set
`ULTRASTEALTH_BYPASSES=on` to additionally layer the (optional) JS spoofs.

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

## MCP Server for Claude Code

The ultrastealth MCP server exposes the stealth browser as tools for Claude Code, giving it the ability to navigate, click, type, screenshot, and monitor network traffic — all with maximum anti-detection.

The server runs as an HTTP service (streamable-http transport) on port **8090** by default, managed by PM2.

### Available Tools

**Browser automation:** `browser_navigate`, `browser_click`, `browser_type`, `browser_get_state`, `browser_screenshot`, `browser_scroll`, `browser_go_back`, `browser_evaluate`, `browser_press_key`, `browser_get_html`, `browser_get`, `browser_is`, `browser_wait`, `browser_hover`, `browser_focus`, `browser_scroll_into_view`, `browser_select_option`, `browser_add_init_script`, `browser_add_script`, `browser_add_style`, `browser_close`

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

## Included Bypasses
The `ultrastealth/bypasses` folder includes injected scripts to spoof webgl, mock canvassing, normalize plugins, bypass `Runtime.enable`, and standard headless fingerprints. These are automatically loaded by the Fetcher.

## Driver Fingerprint Patch (`patch_rebrowser.py`)

`rebrowser-playwright`'s bundled Node driver leaks two identifiers that detectors
(e.g. `bot-detector.rebrowser.net`) probe for in the page context:

- `globalThis.__pwInitScripts` — the init-script dedup map, created by the driver
  *before* any bypass runs (so a JS bypass can't reliably hide it).
- `UtilityScript` — the class wrapping every `page.evaluate`; its name leaks into
  `Error().stack` captured by page JS.

`patch_rebrowser.py` renames both at the driver source (`__pwInitScripts → __execGuards`,
`UtilityScript → ExecutionProxy`), consistently so functionality is preserved. This
lifts the rebrowser bot-detector score from **6/10 → 8/10**.

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
