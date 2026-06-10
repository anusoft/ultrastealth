"""
Ultrastealth MCP Server
========================
Exposes maximum-stealth browser automation as MCP tools for Claude Code.
Uses UltrastealthFetcher (rebrowser-playwright + Xvfb + JS bypasses).

Tools modeled after browser-use MCP:
- browser_navigate, browser_click, browser_type, browser_get_state,
  browser_screenshot, browser_scroll, browser_go_back, browser_evaluate,
  browser_press_key, browser_get_html, browser_wait, browser_close

Network monitoring (like Chrome DevTools Network tab):
- browser_network_enable, browser_network_disable, browser_network_log,
  browser_network_detail, browser_network_response_body, browser_network_clear,
  browser_network_summary

Usage:
    python3 -m ultrastealth.mcp_server                          # HTTP on 0.0.0.0:8090
    python3 -m ultrastealth.mcp_server --port 9000              # HTTP on custom port
    python3 -m ultrastealth.mcp_server --transport stdio         # stdio mode
"""

import asyncio
import base64
import functools
import inspect
import json
import logging
import os
import shutil
import sys
import time
from pathlib import Path

import psutil
from mcp.server.fastmcp import FastMCP

# Add parent dir to path so ultrastealth can be imported
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# CRITICAL: the MCP drives the browser interactively (navigate → get_state →
# click → type), so it needs the FULL Playwright API surface. Under the default
# `alwaysIsolated` rebrowser mode, Runtime.enable is suppressed and every API that
# waits on an execution-context announcement — page.title(), page.content(),
# locator.click()/fill()/inner_text() — DEADLOCKS forever (only evaluate/goto/
# screenshot work). Since every navigate/get_state/status ends in page.title(),
# the server appeared to "always freeze". `addBinding` keeps strong stealth while
# announcing the context, so the standard APIs work. setdefault runs before the
# fetcher's own setdefault(alwaysIsolated), so this wins; override via pm2 env.
os.environ.setdefault("REBROWSER_PATCHES_RUNTIME_FIX_MODE", "addBinding")

from ultrastealth.fetcher import UltrastealthFetcher

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
log = logging.getLogger("ultrastealth.mcp")

mcp = FastMCP(
    "ultrastealth",
    instructions="Maximum-stealth browser automation. Use browser_navigate to open pages, "
    "browser_get_state to see interactive elements, browser_click/browser_type to interact, "
    "browser_screenshot for visual confirmation. All browsing uses stealth anti-detection. "
    "For long-running tasks, periodically call browser_status to check resource usage and "
    "use browser_cleanup to free memory when needed (high RSS, many tabs, long uptime).",
)

# Global browser state
_fetcher: UltrastealthFetcher | None = None
_page = None  # Current active page
_session_start: float | None = None
_request_count: int = 0
_active_tab_id: str | None = None  # Track which tab is active
_browser_wedged: bool = False  # Set when a tool times out; forces a hard restart next call

# Network monitoring state
_network_log: list[dict] = []  # Captured request/response pairs
_network_enabled: bool = False  # Whether network capture is active
_network_max_entries: int = 1000  # Cap to prevent memory bloat
_network_handlers: dict = {}  # page -> (request_handler, response_handler) for cleanup


def _next_request():
    """Increment and return request count."""
    global _request_count
    _request_count += 1
    return _request_count


def _hard_kill_browser():
    """SIGKILL the wedged Chrome process tree and reset browser state.

    Used for crash recovery: never awaits the (possibly hung) CDP connection —
    it kills every chrome process bound to this fetcher's user-data-dir via psutil,
    then nulls the globals so the next _ensure_browser() starts a clean browser.
    The old playwright driver object is abandoned (cheap, GC'd); we do NOT await
    its async stop(), which can itself hang on a dead pipe.
    """
    global _fetcher, _page, _active_tab_id, _network_handlers
    udd = None
    try:
        if _fetcher is not None:
            udd = _fetcher.user_data_dir
    except Exception:
        udd = None
    if udd:
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                cmdline = proc.info.get('cmdline') or []
                name = (proc.info.get('name') or '').lower()
                if ('chrome' in name or 'chromium' in name) and any(udd in a for a in cmdline):
                    proc.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    _fetcher = None
    _page = None
    _active_tab_id = None
    _network_handlers = {}


# Hard ceiling on any single tool call. A wedged browser (e.g. a spinning GPU
# process under Xvfb) used to hang the single shared event loop forever — this is
# the root cause of the "MCP always freezes" symptom. With this wrapper, a stuck
# CDP await is cancelled after the timeout, the browser is force-killed, and the
# next tool call transparently starts a fresh browser instead of freezing.
DEFAULT_TOOL_TIMEOUT = float(os.environ.get("ULTRASTEALTH_TOOL_TIMEOUT", "90"))


def _tool(timeout: float = DEFAULT_TOOL_TIMEOUT):
    """Register an MCP tool with a hard timeout + browser auto-recovery."""
    def decorator(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            global _browser_wedged
            result = fn(*args, **kwargs)
            if not inspect.iscoroutine(result):
                return result
            try:
                return await asyncio.wait_for(result, timeout=timeout)
            except asyncio.TimeoutError:
                _browser_wedged = True
                log.error("Tool %s timed out after %.0fs; killing wedged browser for recovery",
                          fn.__name__, timeout)
                _hard_kill_browser()
                _browser_wedged = False
                return (f"⏱️ '{fn.__name__}' timed out after {timeout:.0f}s — the browser was "
                        "wedged and has been killed. A fresh browser will start automatically on "
                        "your next call; please retry the request.")
        return mcp.tool()(wrapper)
    return decorator


async def _ensure_browser():
    """Lazily start the browser on first tool call."""
    global _fetcher, _page, _session_start, _request_count, _active_tab_id, _browser_wedged
    if _browser_wedged:
        log.warning("Browser flagged wedged; hard-killing before restart")
        _hard_kill_browser()
        _browser_wedged = False
    if _fetcher is None:
        log.info("Starting Ultrastealth browser...")
        _fetcher = UltrastealthFetcher(headless=False)
        await _fetcher.start()
        # Close the default about:blank page that persistent context creates
        default_pages = _fetcher._context.pages
        _page = await _fetcher._context.new_page()
        for dp in default_pages:
            if not dp.is_closed() and dp != _page:
                try:
                    await dp.close()
                except Exception:
                    pass
        _session_start = time.time()
        _request_count = 0
        _active_tab_id = _page_id(_page)
        if _network_enabled:
            _attach_network_listeners(_page)
        log.info("Browser ready.")
    return _fetcher, _page


async def _get_page():
    """Get the current page, starting browser if needed."""
    global _page, _active_tab_id
    _, page = await _ensure_browser()
    if page.is_closed():
        _page = await _fetcher._context.new_page()
        _active_tab_id = _page_id(_page)
        if _network_enabled:
            _attach_network_listeners(_page)
        page = _page
    return page


def _page_id(page) -> str:
    """Short stable ID for a page (last 6 chars of guid or hash of url+index)."""
    # Use object id as stable identifier
    return f"tab_{id(page) & 0xFFFF:04x}"


def _get_browser_pid() -> int | None:
    """Get the Chromium main process PID from the persistent context."""
    try:
        if _fetcher and _fetcher._context:
            # rebrowser-playwright exposes browser via context._browser or _impl_obj
            browser = getattr(_fetcher._context, '_browser', None)
            if browser:
                proc = getattr(browser, '_process', None) or getattr(browser, 'process', None)
                if proc:
                    return proc.pid
            # Try via impl object
            impl = getattr(_fetcher._context, '_impl_obj', None)
            if impl:
                browser = getattr(impl, '_browser', None)
                if browser:
                    proc = getattr(browser, '_process', None)
                    if proc:
                        return proc.pid
    except Exception:
        pass
    # Fallback: find chrome process by user_data_dir in cmdline
    if _fetcher and _fetcher.user_data_dir:
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                cmdline = proc.info.get('cmdline') or []
                if any(_fetcher.user_data_dir in arg for arg in cmdline):
                    name = (proc.info.get('name') or '').lower()
                    if 'chrome' in name or 'chromium' in name:
                        return proc.info['pid']
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    return None


def _get_process_tree(pid: int) -> list[psutil.Process]:
    """Get the browser process and all its children."""
    try:
        parent = psutil.Process(pid)
        return [parent] + parent.children(recursive=True)
    except psutil.NoSuchProcess:
        return []


def _format_bytes(n: int) -> str:
    for unit in ('B', 'KB', 'MB', 'GB'):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _dir_size(path: str) -> int:
    """Get total size of a directory."""
    total = 0
    try:
        for entry in Path(path).rglob('*'):
            if entry.is_file():
                total += entry.stat().st_size
    except (OSError, PermissionError):
        pass
    return total


def _build_element_tree(node, elements, depth=0):
    """Recursively build a flat list of interactive elements from accessibility tree."""
    if node is None:
        return

    role = node.get("role", "")
    name = node.get("name", "")
    value = node.get("value", "")

    # Include interactive elements and text content
    interactive_roles = {
        "link", "button", "textbox", "searchbox", "combobox", "listbox",
        "option", "checkbox", "radio", "slider", "spinbutton", "switch",
        "tab", "menuitem", "menuitemcheckbox", "menuitemradio", "treeitem",
    }

    if role in interactive_roles or (role in ("heading", "img") and name):
        idx = len(elements)
        entry = {"index": idx, "role": role, "name": name}
        if value:
            entry["value"] = value
        # Include useful properties
        for prop in ("checked", "selected", "expanded", "disabled", "level"):
            if prop in node:
                entry[prop] = node[prop]
        elements.append(entry)

    # Recurse into children
    for child in node.get("children", []):
        _build_element_tree(child, elements, depth + 1)


async def _get_interactive_elements(page):
    """Get indexed interactive elements from accessibility tree."""
    try:
        snapshot = await page.accessibility.snapshot()
        if not snapshot:
            return []
        elements = []
        _build_element_tree(snapshot, elements)
        return elements
    except Exception as e:
        log.warning(f"Accessibility snapshot failed: {e}")
        return []


def _format_elements(elements):
    """Format elements into a readable string."""
    if not elements:
        return "(no interactive elements found)"
    lines = []
    for el in elements:
        parts = [f"[{el['index']}]", f"<{el['role']}>"]
        if el.get("name"):
            parts.append(f'"{el["name"]}"')
        if el.get("value"):
            parts.append(f'value="{el["value"]}"')
        for prop in ("checked", "selected", "expanded", "disabled"):
            if el.get(prop) is not None:
                parts.append(f"{prop}={el[prop]}")
        lines.append(" ".join(parts))
    return "\n".join(lines)


# ─── Selector helpers ───────────────────────────────────────────────

async def _resolve_selector(page, index: int | None = None, selector: str | None = None):
    """Resolve an element index (from get_state) or CSS selector to a Playwright locator."""
    if selector:
        return page.locator(selector).first

    if index is not None:
        elements = await _get_interactive_elements(page)
        if index < 0 or index >= len(elements):
            raise ValueError(f"Index {index} out of range (0-{len(elements)-1})")
        el = elements[index]
        role = el["role"]
        name = el.get("name", "")
        return page.get_by_role(role, name=name).first

    raise ValueError("Provide either 'index' (from browser_get_state) or 'selector' (CSS)")


# ─── MCP Tools ──────────────────────────────────────────────────────

@_tool()
async def browser_navigate(url: str) -> str:
    """Navigate to a URL. Returns page title and URL after navigation."""
    page = await _get_page()
    _next_request()
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(2)  # Let JS settle
    except Exception as e:
        return f"Navigation error: {e}"
    title = await page.title()
    return f"Navigated to: {page.url}\nTitle: {title}"


@_tool()
async def browser_get_state(include_screenshot: bool = False) -> str:
    """Get current page state: URL, title, and all interactive elements with indices.
    Use the [index] numbers with browser_click and browser_type."""
    page = await _get_page()
    _next_request()
    title = await page.title()
    elements = await _get_interactive_elements(page)

    result = f"URL: {page.url}\nTitle: {title}\nInteractive elements:\n{_format_elements(elements)}"

    if include_screenshot:
        result += "\n\n(Use browser_screenshot for visual capture)"

    return result


@_tool()
async def browser_click(
    index: int | None = None,
    selector: str | None = None,
    coordinate_x: int | None = None,
    coordinate_y: int | None = None,
) -> str:
    """Click an element by index (from browser_get_state), CSS selector, or coordinates.
    Prefer using index from browser_get_state for reliability."""
    page = await _get_page()
    _next_request()

    try:
        if coordinate_x is not None and coordinate_y is not None:
            await page.mouse.click(coordinate_x, coordinate_y)
            return f"Clicked at ({coordinate_x}, {coordinate_y})"

        locator = await _resolve_selector(page, index=index, selector=selector)
        await locator.click(timeout=10000)
        await asyncio.sleep(1)
        title = await page.title()
        return f"Clicked element. Page: {page.url} | Title: {title}"
    except Exception as e:
        return f"Click failed: {e}"


@_tool()
async def browser_type(
    text: str,
    index: int | None = None,
    selector: str | None = None,
    submit: bool = False,
) -> str:
    """Type text into an input field identified by index or CSS selector.
    Set submit=True to press Enter after typing."""
    page = await _get_page()
    _next_request()

    try:
        locator = await _resolve_selector(page, index=index, selector=selector)
        await locator.fill(text, timeout=10000)
        if submit:
            await locator.press("Enter")
            await asyncio.sleep(2)
        return f"Typed '{text}' into element" + (" and submitted" if submit else "")
    except Exception as e:
        return f"Type failed: {e}"


@_tool()
async def browser_screenshot(full_page: bool = False) -> list:
    """Take a screenshot of the current page. Returns the image for visual analysis."""
    page = await _get_page()
    _next_request()

    try:
        screenshot_bytes = await page.screenshot(full_page=full_page, type="png")
        b64 = base64.b64encode(screenshot_bytes).decode("utf-8")
        return [
            {"type": "text", "text": f"Screenshot of {page.url} ({len(screenshot_bytes)} bytes)"},
            {"type": "image", "data": b64, "mimeType": "image/png"},
        ]
    except Exception as e:
        return [{"type": "text", "text": f"Screenshot failed: {e}"}]


@_tool()
async def browser_scroll(direction: str = "down", amount: int = 500) -> str:
    """Scroll the page. direction: 'up' or 'down'. amount: pixels to scroll (default 500)."""
    page = await _get_page()
    delta = amount if direction == "down" else -amount
    await page.mouse.wheel(0, delta)
    await asyncio.sleep(0.5)
    scroll_y = await page.evaluate("window.scrollY")
    return f"Scrolled {direction} by {amount}px. Current scroll position: {scroll_y}px"


@_tool()
async def browser_go_back() -> str:
    """Navigate back in browser history."""
    page = await _get_page()
    try:
        await page.go_back(timeout=10000)
        await asyncio.sleep(1)
        title = await page.title()
        return f"Went back to: {page.url}\nTitle: {title}"
    except Exception as e:
        return f"Go back failed: {e}"


@_tool()
async def browser_get_html(selector: str | None = None) -> str:
    """Get HTML content of the page or a specific element (via CSS selector)."""
    page = await _get_page()

    try:
        if selector:
            html = await page.locator(selector).first.inner_html(timeout=5000)
        else:
            html = await page.evaluate("document.documentElement.outerHTML")

        # Truncate if too large
        if len(html) > 50000:
            html = html[:50000] + "\n\n... (truncated, 50k char limit)"
        return html
    except Exception as e:
        return f"Get HTML failed: {e}"


@_tool()
async def browser_evaluate(javascript: str) -> str:
    """Execute JavaScript on the current page and return the result."""
    page = await _get_page()

    try:
        result = await page.evaluate(javascript)
        if result is None:
            return "(no return value)"
        if isinstance(result, (dict, list)):
            return json.dumps(result, indent=2, default=str)
        return str(result)
    except Exception as e:
        return f"Evaluate failed: {e}"


@_tool()
async def browser_press_key(key: str) -> str:
    """Press a keyboard key. Examples: 'Enter', 'Tab', 'Escape', 'ArrowDown', 'a', 'Control+c'."""
    page = await _get_page()
    try:
        await page.keyboard.press(key)
        await asyncio.sleep(0.3)
        return f"Pressed key: {key}"
    except Exception as e:
        return f"Key press failed: {e}"


@_tool()
async def browser_wait(
    selector: str | None = None,
    text: str | None = None,
    timeout: int = 10000,
) -> str:
    """Wait for a CSS selector to appear, or for text to appear on the page.
    timeout is in milliseconds (default 10000)."""
    page = await _get_page()

    try:
        if selector:
            await page.wait_for_selector(selector, timeout=timeout)
            return f"Selector '{selector}' appeared"
        elif text:
            await page.get_by_text(text).first.wait_for(timeout=timeout)
            return f"Text '{text}' appeared"
        else:
            await asyncio.sleep(timeout / 1000)
            return f"Waited {timeout}ms"
    except Exception as e:
        return f"Wait failed: {e}"


@_tool()
async def browser_hover(
    index: int | None = None,
    selector: str | None = None,
) -> str:
    """Hover over an element by index or CSS selector."""
    page = await _get_page()
    try:
        locator = await _resolve_selector(page, index=index, selector=selector)
        await locator.hover(timeout=5000)
        return "Hovered over element"
    except Exception as e:
        return f"Hover failed: {e}"


@_tool()
async def browser_select_option(
    values: list[str],
    index: int | None = None,
    selector: str | None = None,
) -> str:
    """Select option(s) from a dropdown/select element."""
    page = await _get_page()
    try:
        locator = await _resolve_selector(page, index=index, selector=selector)
        await locator.select_option(values, timeout=5000)
        return f"Selected option(s): {values}"
    except Exception as e:
        return f"Select failed: {e}"


# ─── Network Monitoring Helpers ──────────────────────────────────────

def _attach_network_listeners(page):
    """Attach request/response listeners to a page for network capture."""
    global _network_handlers

    page_key = id(page)
    if page_key in _network_handlers:
        return  # Already attached

    def on_request(request):
        if len(_network_log) >= _network_max_entries:
            return
        entry = {
            "id": len(_network_log),
            "timestamp": time.time(),
            "method": request.method,
            "url": request.url,
            "resource_type": request.resource_type,
            "request_headers": dict(request.headers) if request.headers else {},
            "post_data": None,
            "status": None,
            "response_headers": {},
            "response_size": None,
            "response_time_ms": None,
            "failed": False,
            "failure_text": None,
        }
        # Capture POST body
        try:
            entry["post_data"] = request.post_data
        except Exception:
            pass
        _network_log.append(entry)

    def on_response(response):
        url = response.url
        # Find the matching request entry (latest one with this URL that has no status yet)
        for entry in reversed(_network_log):
            if entry["url"] == url and entry["status"] is None:
                entry["status"] = response.status
                entry["response_headers"] = dict(response.headers) if response.headers else {}
                try:
                    size_header = response.headers.get("content-length")
                    if size_header:
                        entry["response_size"] = int(size_header)
                except Exception:
                    pass
                entry["response_time_ms"] = round((time.time() - entry["timestamp"]) * 1000, 1)
                break

    def on_request_failed(request):
        url = request.url
        for entry in reversed(_network_log):
            if entry["url"] == url and entry["status"] is None:
                entry["failed"] = True
                entry["failure_text"] = request.failure
                entry["response_time_ms"] = round((time.time() - entry["timestamp"]) * 1000, 1)
                break

    page.on("request", on_request)
    page.on("response", on_response)
    page.on("requestfailed", on_request_failed)
    _network_handlers[page_key] = (on_request, on_response, on_request_failed)


def _detach_network_listeners(page):
    """Remove network listeners from a page."""
    global _network_handlers
    page_key = id(page)
    if page_key in _network_handlers:
        req_h, resp_h, fail_h = _network_handlers[page_key]
        page.remove_listener("request", req_h)
        page.remove_listener("response", resp_h)
        page.remove_listener("requestfailed", fail_h)
        del _network_handlers[page_key]


# ─── Network MCP Tools ──────────────────────────────────────────────

@_tool()
async def browser_network_enable(max_entries: int = 1000) -> str:
    """Start capturing network requests (like opening Chrome DevTools Network tab).
    Call this BEFORE navigating to capture all requests. Captures on all tabs.

    Args:
        max_entries: Maximum number of requests to keep (default 1000, older entries dropped when full)
    """
    global _network_enabled, _network_max_entries, _network_log
    _network_enabled = True
    _network_max_entries = max_entries
    _network_log = []

    # Attach to current page if browser is already running
    if _fetcher and _fetcher._context:
        for p in _fetcher._context.pages:
            if not p.is_closed():
                _attach_network_listeners(p)

    return f"Network capture enabled (max {max_entries} entries). Navigate to a page to start capturing."


@_tool()
async def browser_network_disable() -> str:
    """Stop capturing network requests and remove listeners."""
    global _network_enabled
    _network_enabled = False

    if _fetcher and _fetcher._context:
        for p in _fetcher._context.pages:
            if not p.is_closed():
                _detach_network_listeners(p)
    _network_handlers.clear()

    count = len(_network_log)
    return f"Network capture disabled. {count} entries still in log (use browser_network_clear to free)."


@_tool()
async def browser_network_log(
    filter_url: str | None = None,
    filter_type: str | None = None,
    filter_status: str | None = None,
    filter_method: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> str:
    """Get captured network requests — like the Chrome DevTools Network tab list.

    Args:
        filter_url: Filter by URL substring (e.g. "api", ".js", "graphql")
        filter_type: Filter by resource type: document, stylesheet, script, image, font, xhr, fetch, websocket, other
        filter_status: Filter by status: "2xx", "3xx", "4xx", "5xx", "failed", or exact code like "200"
        filter_method: Filter by HTTP method: GET, POST, PUT, DELETE, etc.
        limit: Max entries to return (default 50)
        offset: Skip first N matching entries (for pagination)

    Returns a table of: ID | Method | Status | Type | Size | Time | URL
    Use the ID with browser_network_detail to see full headers/body."""
    if not _network_log:
        if not _network_enabled:
            return "Network capture is not enabled. Call browser_network_enable first."
        return "No requests captured yet. Navigate to a page first."

    filtered = _network_log
    if filter_url:
        filtered = [e for e in filtered if filter_url.lower() in e["url"].lower()]
    if filter_type:
        filtered = [e for e in filtered if e["resource_type"] == filter_type.lower()]
    if filter_method:
        filtered = [e for e in filtered if e["method"].upper() == filter_method.upper()]
    if filter_status:
        fs = filter_status.lower()
        if fs == "failed":
            filtered = [e for e in filtered if e["failed"]]
        elif fs.endswith("xx"):
            prefix = fs[0]
            filtered = [e for e in filtered if e["status"] and str(e["status"])[0] == prefix]
        else:
            filtered = [e for e in filtered if str(e.get("status", "")) == fs]

    total = len(filtered)
    page_entries = filtered[offset:offset + limit]

    if not page_entries:
        return f"No matching requests (total captured: {len(_network_log)}, filtered: {total})."

    lines = [f"Network Log — {total} matching requests (showing {offset+1}-{offset+len(page_entries)} of {total}, captured: {len(_network_log)})\n"]
    lines.append(f"{'ID':>5} | {'Method':<7} | {'Status':<6} | {'Type':<10} | {'Size':>10} | {'Time':>8} | URL")
    lines.append("-" * 120)

    for e in page_entries:
        status_str = "FAIL" if e["failed"] else str(e["status"] or "...")
        size_str = _format_bytes(e["response_size"]) if e["response_size"] else "-"
        time_str = f"{e['response_time_ms']}ms" if e["response_time_ms"] else "..."
        url_display = e["url"][:80] + "..." if len(e["url"]) > 80 else e["url"]
        lines.append(f"{e['id']:>5} | {e['method']:<7} | {status_str:<6} | {e['resource_type']:<10} | {size_str:>10} | {time_str:>8} | {url_display}")

    if total > offset + limit:
        lines.append(f"\n... {total - offset - limit} more. Use offset={offset + limit} to see next page.")

    return "\n".join(lines)


@_tool()
async def browser_network_detail(request_id: int) -> str:
    """Get full details of a specific network request — like clicking a request in DevTools.

    Shows: URL, method, status, all request/response headers, POST body, timing, and
    optionally the response body (for text-based responses).

    Args:
        request_id: The ID from browser_network_log
    """
    if request_id < 0 or request_id >= len(_network_log):
        return f"Invalid request ID {request_id}. Valid range: 0-{len(_network_log)-1}"

    e = _network_log[request_id]
    lines = ["=== Request Detail ==="]
    lines.append(f"ID: {e['id']}")
    lines.append(f"URL: {e['url']}")
    lines.append(f"Method: {e['method']}")
    lines.append(f"Resource Type: {e['resource_type']}")
    lines.append(f"Status: {'FAILED' if e['failed'] else e['status'] or 'pending'}")
    if e["failure_text"]:
        lines.append(f"Failure: {e['failure_text']}")
    lines.append(f"Time: {e['response_time_ms']}ms" if e["response_time_ms"] else "Time: pending")

    if e["response_size"]:
        lines.append(f"Response Size: {_format_bytes(e['response_size'])}")

    lines.append(f"\n--- Request Headers ---")
    for k, v in e["request_headers"].items():
        lines.append(f"  {k}: {v}")

    if e["post_data"]:
        lines.append(f"\n--- Request Body ---")
        body = e["post_data"]
        # Try to pretty-print JSON
        try:
            parsed = json.loads(body)
            body = json.dumps(parsed, indent=2)
        except (json.JSONDecodeError, TypeError):
            pass
        if len(body) > 5000:
            body = body[:5000] + "\n... (truncated)"
        lines.append(body)

    if e["response_headers"]:
        lines.append(f"\n--- Response Headers ---")
        for k, v in e["response_headers"].items():
            lines.append(f"  {k}: {v}")

    return "\n".join(lines)


@_tool()
async def browser_network_response_body(request_id: int) -> str:
    """Get the response body of a captured network request.
    Only works for requests that are still in the browser's memory.
    Best used right after the request completes — may fail for older requests.

    Args:
        request_id: The ID from browser_network_log
    """
    if request_id < 0 or request_id >= len(_network_log):
        return f"Invalid request ID {request_id}. Valid range: 0-{len(_network_log)-1}"

    e = _network_log[request_id]
    page = await _get_page()

    # Use CDP to get response body if possible
    try:
        cdp = await page.context.new_cdp_session(page)
        # We need the CDP request ID, which we don't have. Try fetching the URL directly.
        # Alternative: use page.evaluate to re-fetch
        pass
    except Exception:
        pass

    # Fallback: use JavaScript fetch to re-request (only for GET, and may differ from original)
    if e["method"] == "GET" and not e["failed"] and e["status"] and e["status"] < 400:
        try:
            content_type = e["response_headers"].get("content-type", "")
            if any(t in content_type for t in ("text", "json", "xml", "javascript", "html", "css")):
                result = await page.evaluate(f"""
                    async () => {{
                        try {{
                            const resp = await fetch({json.dumps(e['url'])});
                            const text = await resp.text();
                            return text.substring(0, 20000);
                        }} catch(e) {{
                            return 'Fetch error: ' + e.message;
                        }}
                    }}
                """)
                if result:
                    lines = [f"Response body for request {request_id} ({e['url'][:80]}):", ""]
                    # Try to pretty-print JSON
                    try:
                        parsed = json.loads(result)
                        result = json.dumps(parsed, indent=2)
                    except (json.JSONDecodeError, TypeError):
                        pass
                    lines.append(result)
                    if len(result) >= 20000:
                        lines.append("\n... (truncated at 20KB)")
                    return "\n".join(lines)
        except Exception as err:
            return f"Could not fetch response body: {err}"

    return (
        f"Cannot retrieve response body for request {request_id} "
        f"(method={e['method']}, status={e['status']}, type={e['resource_type']}). "
        "Response bodies are available for text-based GET requests with successful status codes."
    )


@_tool()
async def browser_network_clear() -> str:
    """Clear all captured network log entries to free memory."""
    global _network_log
    count = len(_network_log)
    _network_log = []
    return f"Cleared {count} network log entries."


@_tool()
async def browser_network_summary() -> str:
    """Get a summary of captured network activity — like the DevTools Network tab footer.
    Shows total requests, data transferred, breakdown by type, status codes, and slowest requests."""
    if not _network_log:
        if not _network_enabled:
            return "Network capture is not enabled. Call browser_network_enable first."
        return "No requests captured yet."

    total = len(_network_log)
    total_size = sum(e["response_size"] or 0 for e in _network_log)
    failed = sum(1 for e in _network_log if e["failed"])
    pending = sum(1 for e in _network_log if not e["failed"] and e["status"] is None)

    # By resource type
    type_counts: dict[str, int] = {}
    type_sizes: dict[str, int] = {}
    for e in _network_log:
        rt = e["resource_type"]
        type_counts[rt] = type_counts.get(rt, 0) + 1
        type_sizes[rt] = type_sizes.get(rt, 0) + (e["response_size"] or 0)

    # By status code range
    status_counts: dict[str, int] = {}
    for e in _network_log:
        if e["failed"]:
            status_counts["failed"] = status_counts.get("failed", 0) + 1
        elif e["status"]:
            bucket = f"{str(e['status'])[0]}xx"
            status_counts[bucket] = status_counts.get(bucket, 0) + 1

    # Slowest requests
    timed = [e for e in _network_log if e["response_time_ms"]]
    slowest = sorted(timed, key=lambda e: e["response_time_ms"], reverse=True)[:10]

    lines = ["=== Network Summary ==="]
    lines.append(f"Total requests: {total}")
    lines.append(f"Data transferred: {_format_bytes(total_size)}")
    if failed:
        lines.append(f"Failed: {failed}")
    if pending:
        lines.append(f"Pending: {pending}")

    lines.append(f"\n--- By Type ---")
    for rt, count in sorted(type_counts.items(), key=lambda x: -x[1]):
        size = _format_bytes(type_sizes.get(rt, 0))
        lines.append(f"  {rt:<12} {count:>4} requests  {size:>10}")

    lines.append(f"\n--- By Status ---")
    for bucket, count in sorted(status_counts.items()):
        lines.append(f"  {bucket:<8} {count:>4} requests")

    if slowest:
        lines.append(f"\n--- Slowest Requests ---")
        for e in slowest:
            url_short = e["url"][:70] + "..." if len(e["url"]) > 70 else e["url"]
            lines.append(f"  {e['response_time_ms']:>8}ms | {e['status'] or 'FAIL'} | {url_short}")

    return "\n".join(lines)


@_tool()
async def browser_close() -> str:
    """Close the browser completely. A new browser will start on next tool call."""
    global _fetcher, _page, _session_start, _request_count, _active_tab_id
    if _fetcher:
        _network_handlers.clear()
        await _fetcher.close()
        _fetcher = None
        _page = None
        _session_start = None
        _request_count = 0
        _active_tab_id = None
        return "Browser closed."
    return "No browser was running."


# ─── Process Control & Resource Management ──────────────────────────

@_tool()
async def browser_status() -> str:
    """Get browser process status: memory usage, CPU, open tabs, user data size, uptime, request count.
    Use this periodically during long-running tasks to decide when to clean up resources.

    Cleanup guidelines:
    - RSS > 500MB: consider closing unused tabs or restarting browser
    - Tabs > 10: close tabs you're done with
    - Uptime > 30min with high memory: restart browser (browser_close + re-navigate)
    - User data > 200MB: use browser_cleanup to clear caches"""
    if _fetcher is None:
        return "Browser not running. Call any browser_* tool to start it."

    _next_request()
    lines = ["=== Browser Status ==="]

    # Session info
    uptime_secs = time.time() - _session_start if _session_start else 0
    uptime_min = uptime_secs / 60
    lines.append(f"Session uptime: {uptime_min:.1f} min ({uptime_secs:.0f}s)")
    lines.append(f"Total requests: {_request_count}")

    # Process info
    pid = _get_browser_pid()
    total_rss = 0
    total_cpu = 0.0
    proc_count = 0
    if pid:
        tree = _get_process_tree(pid)
        proc_count = len(tree)
        for proc in tree:
            try:
                mem = proc.memory_info()
                total_rss += mem.rss
                total_cpu += proc.cpu_percent(interval=0)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        lines.append(f"Browser PID: {pid}")
        lines.append(f"Process count: {proc_count} (main + renderers/GPU/utility)")
        lines.append(f"Total RSS memory: {_format_bytes(total_rss)}")
        # Second CPU sample for more accurate reading
        await asyncio.sleep(0.1)
        total_cpu = 0.0
        for proc in tree:
            try:
                total_cpu += proc.cpu_percent(interval=0)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        lines.append(f"Total CPU: {total_cpu:.1f}%")
    else:
        lines.append("Browser PID: (not found)")

    # System memory context
    vm = psutil.virtual_memory()
    lines.append(f"System memory: {_format_bytes(vm.used)}/{_format_bytes(vm.total)} ({vm.percent}% used)")

    # Tabs/pages
    if _fetcher and _fetcher._context:
        pages = _fetcher._context.pages
        lines.append(f"\n=== Open Tabs ({len(pages)}) ===")
        for p in pages:
            tab_id = _page_id(p)
            active = " [ACTIVE]" if tab_id == _active_tab_id else ""
            closed = " [CLOSED]" if p.is_closed() else ""
            try:
                url = p.url if not p.is_closed() else "(closed)"
                title = await p.title() if not p.is_closed() else ""
            except Exception:
                url = "(error)"
                title = ""
            title_short = title[:50] + "..." if len(title) > 50 else title
            lines.append(f"  {tab_id}{active}{closed}: {url}")
            if title_short:
                lines.append(f"    title: {title_short}")

    # User data directory
    if _fetcher and _fetcher.user_data_dir:
        udd = _fetcher.user_data_dir
        udd_size = _dir_size(udd)
        lines.append(f"\n=== User Data ===")
        lines.append(f"Path: {udd}")
        lines.append(f"Size: {_format_bytes(udd_size)}")
        is_temp = _fetcher._temp_dir is not None
        lines.append(f"Type: {'temporary (auto-cleaned on close)' if is_temp else 'persistent'}")

        # Break down key subdirectories
        for subdir in ('Default/Cache', 'Default/Code Cache', 'Default/Service Worker',
                       'Default/GPUCache', 'Default/IndexedDB', 'Default/Local Storage'):
            sub_path = os.path.join(udd, subdir)
            if os.path.exists(sub_path):
                sub_size = _dir_size(sub_path)
                if sub_size > 1024 * 1024:  # Only show if > 1MB
                    lines.append(f"  {subdir}: {_format_bytes(sub_size)}")

    # Health warnings
    warnings = []
    if total_rss > 500 * 1024 * 1024:
        warnings.append("HIGH MEMORY: RSS > 500MB — consider closing tabs or restarting")
    if _fetcher and _fetcher._context and len(_fetcher._context.pages) > 10:
        warnings.append("MANY TABS: >10 open — close unused tabs with browser_close_tab")
    if uptime_min > 30 and total_rss > 300 * 1024 * 1024:
        warnings.append("LONG SESSION + HIGH MEMORY: Consider browser restart (close + re-navigate)")
    if _fetcher and _fetcher.user_data_dir:
        udd_size = _dir_size(_fetcher.user_data_dir)
        if udd_size > 200 * 1024 * 1024:
            warnings.append("LARGE USER DATA: >200MB — use browser_cleanup to clear caches")

    if warnings:
        lines.append(f"\n=== Warnings ===")
        for w in warnings:
            lines.append(f"  ⚠ {w}")

    return "\n".join(lines)


@_tool()
async def browser_list_tabs() -> str:
    """List all open browser tabs with their IDs, URLs, titles, and memory estimates.
    Use tab IDs with browser_switch_tab and browser_close_tab."""
    if not _fetcher or not _fetcher._context:
        return "Browser not running."

    _next_request()
    pages = _fetcher._context.pages
    if not pages:
        return "No tabs open."

    lines = [f"Open tabs: {len(pages)}\n"]
    for i, p in enumerate(pages):
        tab_id = _page_id(p)
        active = " [ACTIVE]" if tab_id == _active_tab_id else ""
        if p.is_closed():
            lines.append(f"  {tab_id}{active} [CLOSED]")
            continue
        try:
            url = p.url
            title = await p.title()
        except Exception:
            url = "(error)"
            title = ""

        lines.append(f"  {tab_id}{active}: {url}")
        if title:
            lines.append(f"    title: {title}")

    return "\n".join(lines)


@_tool()
async def browser_switch_tab(tab_id: str) -> str:
    """Switch to a different tab by its ID (from browser_list_tabs).
    The switched-to tab becomes the active page for all subsequent tool calls."""
    global _page, _active_tab_id
    if not _fetcher or not _fetcher._context:
        return "Browser not running."

    _next_request()
    for p in _fetcher._context.pages:
        if _page_id(p) == tab_id and not p.is_closed():
            _page = p
            _active_tab_id = tab_id
            await p.bring_to_front()
            title = await p.title()
            return f"Switched to {tab_id}: {p.url}\nTitle: {title}"

    available = [_page_id(p) for p in _fetcher._context.pages if not p.is_closed()]
    return f"Tab '{tab_id}' not found. Available: {', '.join(available)}"


@_tool()
async def browser_close_tab(tab_id: str) -> str:
    """Close a specific tab by its ID. If closing the active tab, switches to another open tab.
    Use browser_list_tabs to see tab IDs."""
    global _page, _active_tab_id
    if not _fetcher or not _fetcher._context:
        return "Browser not running."

    _next_request()
    for p in _fetcher._context.pages:
        if _page_id(p) == tab_id and not p.is_closed():
            was_active = (tab_id == _active_tab_id)
            await p.close()

            if was_active:
                # Switch to another open tab
                open_pages = [pg for pg in _fetcher._context.pages if not pg.is_closed()]
                if open_pages:
                    _page = open_pages[-1]
                    _active_tab_id = _page_id(_page)
                    title = await _page.title()
                    return f"Closed {tab_id}. Switched to {_active_tab_id}: {_page.url} ({title})"
                else:
                    _page = await _fetcher._context.new_page()
                    _active_tab_id = _page_id(_page)
                    return f"Closed {tab_id}. Opened new blank tab {_active_tab_id}."
            return f"Closed {tab_id}."

    return f"Tab '{tab_id}' not found."


@_tool()
async def browser_new_tab(url: str | None = None) -> str:
    """Open a new tab, optionally navigating to a URL. Returns the new tab's ID."""
    global _page, _active_tab_id
    await _ensure_browser()
    _next_request()

    new_page = await _fetcher._context.new_page()
    _page = new_page
    _active_tab_id = _page_id(new_page)
    if _network_enabled:
        _attach_network_listeners(new_page)

    if url:
        try:
            await new_page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(2)
        except Exception as e:
            return f"New tab {_active_tab_id} opened but navigation failed: {e}"
        title = await new_page.title()
        return f"New tab {_active_tab_id}: {new_page.url}\nTitle: {title}"

    return f"New blank tab {_active_tab_id} opened."


@_tool()
async def browser_cleanup(
    close_blank_tabs: bool = True,
    clear_cache: bool = True,
    clear_storage: bool = False,
    close_tabs_except_active: bool = False,
) -> str:
    """Clean up browser resources to free memory during long-running sessions.

    Args:
        close_blank_tabs: Close tabs with about:blank URL (default True)
        clear_cache: Clear browser disk cache (default True)
        clear_storage: Clear localStorage/sessionStorage on all pages (default False, destructive)
        close_tabs_except_active: Close ALL tabs except the active one (default False)
    """
    global _page, _active_tab_id
    if not _fetcher or not _fetcher._context:
        return "Browser not running."

    _next_request()
    actions = []

    # Get memory before
    pid = _get_browser_pid()
    rss_before = 0
    if pid:
        for proc in _get_process_tree(pid):
            try:
                rss_before += proc.memory_info().rss
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

    # Close blank tabs
    if close_blank_tabs:
        closed = 0
        for p in list(_fetcher._context.pages):
            if not p.is_closed() and p.url in ("about:blank", "") and _page_id(p) != _active_tab_id:
                await p.close()
                closed += 1
        if closed:
            actions.append(f"Closed {closed} blank tab(s)")

    # Close all tabs except active
    if close_tabs_except_active:
        closed = 0
        for p in list(_fetcher._context.pages):
            if not p.is_closed() and _page_id(p) != _active_tab_id:
                await p.close()
                closed += 1
        if closed:
            actions.append(f"Closed {closed} non-active tab(s)")

    # Clear storage on open pages
    if clear_storage:
        cleared = 0
        for p in _fetcher._context.pages:
            if not p.is_closed() and p.url not in ("about:blank", ""):
                try:
                    await p.evaluate("localStorage.clear(); sessionStorage.clear();")
                    cleared += 1
                except Exception:
                    pass
        if cleared:
            actions.append(f"Cleared storage on {cleared} page(s)")

    # Clear disk caches
    if clear_cache and _fetcher.user_data_dir:
        cache_dirs = ['Default/Cache', 'Default/Code Cache', 'Default/GPUCache',
                      'Default/Service Worker/CacheStorage']
        freed = 0
        for subdir in cache_dirs:
            cache_path = os.path.join(_fetcher.user_data_dir, subdir)
            if os.path.exists(cache_path):
                size = _dir_size(cache_path)
                try:
                    shutil.rmtree(cache_path, ignore_errors=True)
                    os.makedirs(cache_path, exist_ok=True)
                    freed += size
                except OSError:
                    pass
        if freed:
            actions.append(f"Cleared {_format_bytes(freed)} of disk cache")

    # Ensure active page still valid
    if _page and _page.is_closed():
        open_pages = [p for p in _fetcher._context.pages if not p.is_closed()]
        if open_pages:
            _page = open_pages[-1]
            _active_tab_id = _page_id(_page)
        else:
            _page = await _fetcher._context.new_page()
            _active_tab_id = _page_id(_page)

    # Get memory after
    rss_after = 0
    if pid:
        # Small delay for OS to reclaim
        await asyncio.sleep(0.5)
        for proc in _get_process_tree(pid):
            try:
                rss_after += proc.memory_info().rss
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

    if not actions:
        actions.append("Nothing to clean up")

    tab_count = len([p for p in _fetcher._context.pages if not p.is_closed()])
    result = "Cleanup complete:\n" + "\n".join(f"  - {a}" for a in actions)
    result += f"\nRemaining tabs: {tab_count}"
    if rss_before and rss_after:
        diff = rss_before - rss_after
        result += f"\nMemory: {_format_bytes(rss_before)} → {_format_bytes(rss_after)}"
        if diff > 0:
            result += f" (freed {_format_bytes(diff)})"
    return result


@_tool()
async def browser_restart(navigate_to: str | None = None) -> str:
    """Restart the browser to reclaim all memory. Optionally navigate to a URL after restart.
    Use this when memory is high and closing tabs isn't enough."""
    global _fetcher, _page, _session_start, _request_count, _active_tab_id

    _next_request()
    old_url = None
    if _page and not _page.is_closed():
        old_url = _page.url

    if _fetcher:
        _network_handlers.clear()
        await _fetcher.close()
        _fetcher = None
        _page = None

    _fetcher = UltrastealthFetcher(headless=False)
    await _fetcher.start()
    _page = await _fetcher._context.new_page()
    _session_start = time.time()
    _request_count = 0
    _active_tab_id = _page_id(_page)
    if _network_enabled:
        _attach_network_listeners(_page)

    target = navigate_to or old_url
    if target and target not in ("about:blank", ""):
        try:
            await _page.goto(target, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(2)
            title = await _page.title()
            return f"Browser restarted. Navigated to: {_page.url}\nTitle: {title}"
        except Exception as e:
            return f"Browser restarted but navigation failed: {e}"

    return "Browser restarted with a clean session."


# ─── Entry point ────────────────────────────────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(description="Ultrastealth MCP Server")
    parser.add_argument(
        "--transport", choices=["stdio", "streamable-http"], default="streamable-http",
        help="MCP transport (default: streamable-http)",
    )
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8090, help="Port to bind (default: 8090)")
    args = parser.parse_args()

    if args.transport == "stdio":
        mcp.run(transport="stdio")
    else:
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
