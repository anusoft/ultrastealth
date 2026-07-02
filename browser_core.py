"""Shared warm-browser engine.

Canonical, front-end-agnostic browser operations returning JSON-serializable
dicts. Owned by the daemon; also importable directly by the MCP server. Stealth
and profile behavior is delegated entirely to UltrastealthFetcher — this module
never changes the launch/bypass path.

A `target` argument is either a snapshot ref matching ``^e\\d+$`` (resolved via
the current tab's ref-map) or a CSS selector (anything else).
"""
import asyncio
import base64
import os
import re
import sys
import time
from pathlib import Path

os.environ.setdefault("REBROWSER_PATCHES_RUNTIME_FIX_MODE", "addBinding")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ultrastealth.fetcher import UltrastealthFetcher

_REF_RE = re.compile(r"^e\d+$")

# ── Module state (single warm browser) ──────────────────────────────
_fetcher = None
_page = None
_browser_config = (None, None, None)
_session_start = None
_ref_maps: dict = {}              # tab_id -> {ref: entry}
_prev_ref_signatures: dict = {}   # tab_id -> set(signatures) for --diff
_op_lock = asyncio.Lock()


class BrowserCoreError(Exception):
    def __init__(self, type_: str, message: str):
        super().__init__(message)
        self.type = type_
        self.message = message


def reset_state_for_tests(fetcher=None, page=None):
    """Inject fakes / clear state. Test-only."""
    global _fetcher, _page, _browser_config, _session_start, _ref_maps, _prev_ref_signatures
    _fetcher = fetcher
    _page = page
    _browser_config = (None, None, None)
    _session_start = time.time()
    _ref_maps = {}
    _prev_ref_signatures = {}


def _page_id(page) -> str:
    guid = getattr(page, "_guid", None) or str(id(page))
    return guid[-6:] if len(guid) >= 6 else guid


async def get_page():
    """Return the active page (assumes browser already started or injected)."""
    if _page is None:
        raise BrowserCoreError("no_browser", "Browser is not started")
    return _page


async def status() -> dict:
    return {
        "warm": _page is not None and not _page.is_closed(),
        "url": _page.url if _page is not None else None,
        "uptime_s": (time.time() - _session_start) if _session_start else 0,
        "tabs": len(_fetcher._context.pages) if _fetcher is not None else 0,
    }


# ── Snapshot + ref-map ──────────────────────────────────────────────
_INTERACTIVE_ROLES = {
    "link", "button", "textbox", "searchbox", "combobox", "listbox",
    "option", "checkbox", "radio", "slider", "spinbutton", "switch",
    "tab", "menuitem", "menuitemcheckbox", "menuitemradio", "treeitem",
}


def _walk_tree(node, out):
    if node is None:
        return
    role = node.get("role", "")
    name = node.get("name", "")
    if role in _INTERACTIVE_ROLES or (role in ("heading", "img") and name):
        entry = {"role": role, "name": name}
        for prop in ("value", "checked", "selected", "expanded", "disabled", "level"):
            if node.get(prop) not in (None, ""):
                entry[prop] = node[prop]
        out.append(entry)
    for child in node.get("children", []):
        _walk_tree(child, out)


async def snapshot(interactive: bool = True, compact: bool = True,
                   diff: bool = False, tab: str | None = None) -> dict:
    page = await get_page()
    tab_id = tab or _page_id(page)
    try:
        tree = await page.accessibility.snapshot()
    except Exception as e:
        raise BrowserCoreError("snapshot_failed", str(e))
    raw = []
    _walk_tree(tree or {}, raw)

    # Assign stable e-refs; occurrence disambiguates duplicate role+name.
    seen = {}
    ref_map, refs = {}, []
    for i, el in enumerate(raw):
        key = (el["role"], el["name"])
        occ = seen.get(key, 0)
        seen[key] = occ + 1
        ref = f"e{i}"
        entry = {"ref": ref, "occurrence": occ, **el}
        ref_map[ref] = entry
        refs.append(entry)
    _ref_maps[tab_id] = ref_map

    signatures = {f'{e["role"]}|{e["name"]}|{e["occurrence"]}' for e in refs}
    if diff:
        prev = _prev_ref_signatures.get(tab_id, set())
        refs = [e for e in refs
                if f'{e["role"]}|{e["name"]}|{e["occurrence"]}' not in prev]
    _prev_ref_signatures[tab_id] = signatures

    out = {"url": page.url, "title": await page.title(), "refs": refs}
    if not compact:
        out["tree"] = tree
    return out


async def _resolve(page, target: str):
    """Resolve a ref (eN) or CSS selector to a Playwright locator."""
    if _REF_RE.match(target or ""):
        ref_map = _ref_maps.get(_page_id(page), {})
        entry = ref_map.get(target)
        if entry is None:
            raise BrowserCoreError(
                "stale_ref",
                f"Ref {target!r} not in the current snapshot — call snapshot again.",
            )
        return page.get_by_role(entry["role"], name=entry["name"]).nth(entry["occurrence"])
    return page.locator(target).first


# ── Navigation + mutating actions ───────────────────────────────────
async def _maybe_snapshot(result: dict, snapshot_after: bool) -> dict:
    if snapshot_after:
        result["snapshot"] = await snapshot()
    return result


async def navigate(url: str, wait_secs: float = 2.0, snapshot_after: bool = False) -> dict:
    page = await get_page()
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
    except Exception as e:
        raise BrowserCoreError("navigation_failed", str(e))
    if wait_secs:
        await asyncio.sleep(wait_secs)
    return await _maybe_snapshot({"url": page.url, "title": await page.title()}, snapshot_after)


async def reload(snapshot_after: bool = False) -> dict:
    page = await get_page()
    await page.reload(wait_until="domcontentloaded", timeout=30000)
    return await _maybe_snapshot({"url": page.url, "title": await page.title()}, snapshot_after)


async def go_back(snapshot_after: bool = False) -> dict:
    page = await get_page()
    await page.go_back(wait_until="domcontentloaded", timeout=30000)
    return await _maybe_snapshot({"url": page.url, "title": await page.title()}, snapshot_after)


async def click(target: str, snapshot_after: bool = False) -> dict:
    page = await get_page()
    await (await _resolve(page, target)).click(timeout=10000)
    return await _maybe_snapshot({"clicked": target}, snapshot_after)


async def type_text(target: str, text: str, submit: bool = False,
                    snapshot_after: bool = False) -> dict:
    page = await get_page()
    loc = await _resolve(page, target)
    await loc.type(text, delay=20, timeout=10000)
    if submit:
        await loc.press("Enter", timeout=10000)
    return await _maybe_snapshot({"typed": target}, snapshot_after)


async def fill(target: str, text: str, snapshot_after: bool = False) -> dict:
    page = await get_page()
    await (await _resolve(page, target)).fill(text, timeout=10000)
    return await _maybe_snapshot({"filled": target}, snapshot_after)


async def press(key: str, target: str | None = None, snapshot_after: bool = False) -> dict:
    page = await get_page()
    if target:
        await (await _resolve(page, target)).press(key, timeout=10000)
    else:
        await page.keyboard.press(key)
    return await _maybe_snapshot({"pressed": key}, snapshot_after)


async def hover(target: str, snapshot_after: bool = False) -> dict:
    page = await get_page()
    await (await _resolve(page, target)).hover(timeout=10000)
    return await _maybe_snapshot({"hovered": target}, snapshot_after)


async def focus(target: str, snapshot_after: bool = False) -> dict:
    page = await get_page()
    await (await _resolve(page, target)).focus(timeout=10000)
    return await _maybe_snapshot({"focused": target}, snapshot_after)


async def scroll_into_view(target: str, snapshot_after: bool = False) -> dict:
    page = await get_page()
    await (await _resolve(page, target)).scroll_into_view_if_needed(timeout=10000)
    return await _maybe_snapshot({"scrolled_into_view": target}, snapshot_after)


async def select_option(target: str, value: str, snapshot_after: bool = False) -> dict:
    page = await get_page()
    await (await _resolve(page, target)).select_option(value, timeout=10000)
    return await _maybe_snapshot({"selected": target, "value": value}, snapshot_after)


async def scroll(direction: str = "down", amount: int = 500,
                 snapshot_after: bool = False) -> dict:
    page = await get_page()
    dy = amount if direction == "down" else -amount
    await page.evaluate(f"window.scrollBy(0, {dy})")
    return await _maybe_snapshot({"scrolled": direction, "amount": amount}, snapshot_after)


# ── Inspectors ──────────────────────────────────────────────────────
async def get(kind: str, target: str | None = None, attribute: str | None = None) -> dict:
    page = await get_page()
    if kind == "url":
        return {"url": page.url}
    if kind == "title":
        return {"title": await page.title()}
    loc = await _resolve(page, target)
    if kind == "text":
        return {"text": (await loc.text_content()) or ""}
    if kind == "html":
        return {"html": await loc.inner_html()}
    if kind == "attr":
        return {"attr": await loc.get_attribute(attribute)}
    raise BrowserCoreError("bad_arg", f"Unknown get kind: {kind}")


async def is_(kind: str, target: str) -> dict:
    page = await get_page()
    loc = await _resolve(page, target)
    if kind == "visible":
        return {"result": await loc.is_visible()}
    if kind == "enabled":
        return {"result": await loc.is_enabled()}
    if kind == "checked":
        return {"result": await loc.is_checked()}
    raise BrowserCoreError("bad_arg", f"Unknown is kind: {kind}")


async def wait(selector: str | None = None, text: str | None = None,
               url_contains: str | None = None, load_state: str | None = None,
               javascript: str | None = None, timeout_ms: int = 10000) -> dict:
    page = await get_page()
    if selector:
        await page.wait_for_selector(selector, timeout=timeout_ms)
        return {"waited": "selector", "value": selector}
    if url_contains:
        await page.wait_for_url(f"**{url_contains}**", timeout=timeout_ms)
        return {"waited": "url", "value": url_contains}
    if load_state:
        await page.wait_for_load_state(load_state, timeout=timeout_ms)
        return {"waited": "load_state", "value": load_state}
    if javascript:
        await page.wait_for_function(javascript, timeout=timeout_ms)
        return {"waited": "function", "value": javascript}
    if text:
        await page.wait_for_selector(f"text={text}", timeout=timeout_ms)
        return {"waited": "text", "value": text}
    raise BrowserCoreError(
        "bad_arg", "wait requires one of selector/text/url_contains/load_state/javascript")


async def evaluate(javascript: str) -> dict:
    page = await get_page()
    return {"result": await page.evaluate(javascript)}


async def screenshot(full_page: bool = False, path: str | None = None) -> dict:
    page = await get_page()
    data = await page.screenshot(full_page=full_page)
    if path:
        out = Path(path).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
        return {"path": str(out), "bytes": len(data)}
    return {"base64": base64.b64encode(data).decode(), "bytes": len(data)}


# ── Op registry + batch ─────────────────────────────────────────────
OPS = {
    "navigate": navigate, "reload": reload, "go_back": go_back,
    "snapshot": snapshot, "click": click, "type": type_text, "fill": fill,
    "press": press, "hover": hover, "focus": focus,
    "scroll_into_view": scroll_into_view, "select": select_option,
    "scroll": scroll, "get": get, "is": is_, "wait": wait,
    "evaluate": evaluate, "screenshot": screenshot, "status": status,
}


async def batch(steps: list, stop_on_error: bool = True) -> dict:
    results = []
    for step in steps:
        op = step.get("op")
        fn = OPS.get(op)
        if fn is None:
            results.append({"ok": False, "op": op,
                            "error": {"type": "bad_op", "message": f"Unknown op {op!r}"}})
            if stop_on_error:
                break
            continue
        args = {k: v for k, v in step.items() if k != "op"}
        try:
            results.append({"ok": True, "op": op, "result": await fn(**args)})
        except BrowserCoreError as e:
            results.append({"ok": False, "op": op,
                            "error": {"type": e.type, "message": e.message}})
            if stop_on_error:
                break
        except Exception as e:  # noqa: BLE001 — surface any driver error as a step failure
            results.append({"ok": False, "op": op,
                            "error": {"type": "error", "message": str(e)}})
            if stop_on_error:
                break
    return {"steps": results}


OPS["batch"] = batch


# ── Real browser lifecycle ──────────────────────────────────────────
async def ensure_browser(runner: str | None = None, user_data_dir: str | None = None,
                         profile_directory: str | None = None) -> None:
    # Lazy import avoids an import cycle with mcp_server (which imports this module).
    from mcp_server import _profile_config, _fetcher_kwargs

    global _fetcher, _page, _browser_config, _session_start
    config = _profile_config(runner, user_data_dir, profile_directory)
    if _fetcher is None:
        _fetcher = UltrastealthFetcher(**_fetcher_kwargs(config))
        await _fetcher.start()
        default_pages = list(_fetcher._context.pages)
        _page = await _fetcher._context.new_page()
        for dp in default_pages:
            if not dp.is_closed() and dp is not _page:
                try:
                    await dp.close()
                except Exception:
                    pass
        _session_start = time.time()
        _browser_config = config


async def close() -> dict:
    global _fetcher, _page, _ref_maps, _prev_ref_signatures
    if _fetcher is not None:
        try:
            await _fetcher.close()
        except Exception:
            pass
    _fetcher = None
    _page = None
    _ref_maps = {}
    _prev_ref_signatures = {}
    return {"closed": True}
