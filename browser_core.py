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
import difflib
import os
import re
import sys
import time
from pathlib import Path

import psutil

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

# Per-session op lock. Today there is exactly one browser session, so every
# caller resolves to the same _DEFAULT_SESSION lock — behaviorally identical
# to the single module-global asyncio.Lock() this replaces. Keying by session
# now (instead of hanging one Lock() off the module) means a future
# multi-session daemon can hand each session its own serialization lock
# without having to touch every call site that currently does
# `async with browser_core._op_lock`.
_DEFAULT_SESSION = "default"
_op_locks: dict[str, asyncio.Lock] = {}


def get_op_lock(session: str | None = None) -> asyncio.Lock:
    """Return the op lock for `session`, creating it on first use.

    `session` is optional and defaults to the single shared session — callers
    that don't yet know about sessions (i.e. everything today) keep getting
    the one lock every op has always shared.
    """
    key = session or _DEFAULT_SESSION
    lock = _op_locks.get(key)
    if lock is None:
        lock = _op_locks[key] = asyncio.Lock()
    return lock


class BrowserCoreError(Exception):
    def __init__(self, type_: str, message: str):
        super().__init__(message)
        self.type = type_
        self.message = message


def reset_state_for_tests(fetcher=None, page=None):
    """Inject fakes / clear state. Test-only."""
    global _fetcher, _page, _browser_config, _session_start, _ref_maps, _prev_ref_signatures, _op_locks
    _fetcher = fetcher
    _page = page
    _browser_config = (None, None, None)
    _session_start = time.time()
    _ref_maps = {}
    _prev_ref_signatures = {}
    _op_locks = {}


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


# ── Health check ─────────────────────────────────────────────────────
def _process_alive(fetcher) -> bool | None:
    """OS-level liveness check for `fetcher`'s browser process tree.

    Mirrors the matching convention mcp_server._hard_kill_browser already uses
    for crash recovery (process name contains chrome/chromium AND its cmdline
    references this fetcher's user_data_dir) — deliberately reusing that
    heuristic rather than inventing a second one. This is independent of the
    CDP connection: a process can be alive but wedged, or the CDP pipe can
    look intact for a moment after the process is already gone.

    Returns None (indeterminate — callers should not treat this as "exited")
    when liveness can't be established, e.g. no user_data_dir is known yet.
    """
    udd = getattr(fetcher, "user_data_dir", None) if fetcher is not None else None
    if not udd:
        return None
    for proc in psutil.process_iter(["name", "cmdline"]):
        try:
            cmdline = proc.info.get("cmdline") or []
            name = (proc.info.get("name") or "").lower()
            if ("chrome" in name or "chromium" in name) and any(udd in a for a in cmdline):
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return False


async def health_check(cdp_timeout: float = 10.0) -> dict:
    """Diagnose the current browser session with two independent signals.

    Distinguishes three states the prior single `page.title()` ping conflated:
      - "process_exited": the OS-level Chrome/Chromium process is gone
        (crashed or killed out-of-band). The CDP pipe has nothing to talk to
        either way, so the CDP probe is skipped rather than waiting out a
        timeout against a dead connection.
      - "unresponsive": the OS process is still alive but a lightweight CDP
        call (page.title()) did not return within `cdp_timeout` — a wedged
        browser/renderer, not a missing one.
      - "healthy": the process is alive and CDP answered in time.

    Raises BrowserCoreError("no_browser", ...) via get_page() when there is no
    browser session at all, same as every other inspector in this module.
    """
    page = await get_page()
    process_alive = _process_alive(_fetcher)
    if process_alive is False:
        return {"state": "process_exited", "process_alive": False, "cdp_ok": False}
    try:
        await asyncio.wait_for(page.title(), timeout=cdp_timeout)
        return {"state": "healthy", "process_alive": process_alive, "cdp_ok": True}
    except Exception:
        return {"state": "unresponsive", "process_alive": process_alive, "cdp_ok": False}


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


def _match_score(query: str, entry: dict) -> float:
    """Score how well `entry` (a snapshot ref: role + accessible name) matches
    a natural-language-ish `query`. Pure stdlib text similarity — no LLM call,
    no network, no new dependency — combining:
      - difflib.SequenceMatcher ratio as a fuzzy baseline (handles typos/partial
        matches),
      - a substring bonus when the query appears verbatim in the name,
      - an exact-match bonus for an exact name/role hit,
      - a token-overlap bonus so multi-word queries reward matching words
        regardless of order.
    Deterministic and synchronous; safe to call in a tight loop over a
    snapshot's refs.
    """
    q = (query or "").strip().lower()
    name = (entry.get("name") or "").strip().lower()
    role = (entry.get("role") or "").strip().lower()
    haystack = f"{role} {name}".strip()
    if not q or not haystack:
        return 0.0
    score = difflib.SequenceMatcher(None, q, haystack).ratio()
    if name and q in name:
        score += 0.3
    if q == name or q == role:
        score += 0.5
    q_tokens = set(q.split())
    if q_tokens:
        hay_tokens = set(haystack.split())
        score += 0.4 * (len(q_tokens & hay_tokens) / len(q_tokens))
    return round(score, 4)


async def find(query: str) -> dict:
    """Return the single best-matching interactive element ref for `query`.

    Matches against the current accessibility snapshot's existing labels
    (role + accessible name) using a lightweight, synchronous, dependency-free
    text-similarity score (see `_match_score`) — NOT an LLM call. Refreshes
    the snapshot first so `find` works standalone without a prior `snapshot`
    step. Always returns its top pick plus a `score` so the caller can decide
    whether the match is good enough; raises only when there is nothing to
    match against at all.
    """
    snap = await snapshot()
    refs = snap["refs"]
    if not refs:
        raise BrowserCoreError("no_matches", "No interactive elements in the current snapshot")
    best = max(refs, key=lambda e: _match_score(query, e))
    score = _match_score(query, best)
    label = f'{best["role"]} "{best["name"]}"' if best.get("name") else best["role"]
    return {"ref": best["ref"], "label": label, "score": score,
            "role": best["role"], "name": best.get("name", "")}


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


async def cookies() -> dict:
    """Return the current browser context's cookies (context.cookies())."""
    page = await get_page()
    try:
        return {"cookies": await page.context.cookies()}
    except Exception as e:
        raise BrowserCoreError("cookies_failed", str(e))


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
    "cookies": cookies, "find": find,
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
    from mcp_server import (
        _fetcher_kwargs,
        _profile_args_supplied,
        _profile_config,
        _profile_requested,
    )

    global _fetcher, _page, _browser_config, _session_start
    if (
        not _profile_args_supplied(runner, user_data_dir, profile_directory)
        and _profile_requested(_browser_config)
    ):
        config = _browser_config
    else:
        config = _profile_config(runner, user_data_dir, profile_directory)
    if _fetcher is not None and _profile_requested(config) and config != _browser_config:
        await close()
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
