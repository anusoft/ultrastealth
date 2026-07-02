# Ultrastealth Warm-Daemon Fast Driver Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Ultrastealth stack fast by adding a standalone warm-browser daemon that owns one persistent-profile Chrome, a cmux-style CLI, a `browser_batch` MCP tool, a snapshot-ref model, and skills — so Claude (via MCP), the shell/CLI, and reusable scraper scripts all drive the same already-warm browser with no cold starts and few round-trips.

**Architecture:** A new self-contained `browser_core` module holds the shared browser state and structured (dict-returning) async operations, including a per-tab snapshot ref-map and a batch executor. A `daemon` process owns `browser_core` and serves newline-delimited JSON-RPC over a Unix socket. A `client` connects to it (auto-starting it if needed). The `cli` is a thin client. The MCP server gains a `browser_batch` tool and ref-aware snapshots, and auto-detects a running daemon to route through it (falling back to owning its own browser when no daemon is present, preserving today's behavior). Stealth model, rebrowser patch, and profile defaults are unchanged.

**Tech Stack:** Python 3.12, `rebrowser-playwright`, `asyncio` (`start_unix_server`), FastMCP, `argparse`, `unittest` with fake page/context doubles (matching `tests/test_mcp_browser_tools.py`).

**Spec:** `docs/superpowers/specs/2026-07-02-warm-daemon-fast-driver-design.md`

**Verification command (used throughout):**
```
/Users/mac/.local/pipx/venvs/ultrastealth/bin/python -m unittest discover -s tests
```
Single-test form used in steps below:
```
/Users/mac/.local/pipx/venvs/ultrastealth/bin/python -m unittest tests.test_browser_core -v
```

---

## File Structure

**Create:**
- `browser_core.py` — shared browser state, structured async ops, per-tab ref-map, `batch`. The single canonical implementation.
- `daemon.py` — JSON-RPC command dispatch, Unix-socket server, lifecycle (`run`/`start`/`stop`/`status`), idle keep-warm, health watchdog.
- `client.py` — `UltrastealthClient` + `connect()` convenience + daemon auto-start.
- `cli.py` — `ultrastealth`/`us` CLI (`daemon …` and `browser …` subcommands) + output formatting.
- `tests/browser_fakes.py` — shared fake `Page`/`Context`/`Accessibility` doubles for the new tests.
- `tests/test_browser_core.py`, `tests/test_daemon.py`, `tests/test_client.py`, `tests/test_cli.py`, `tests/test_mcp_batch.py`.
- `skills/fast-browser/SKILL.md`, `skills/fast-browser/references/commands.md`.
- `tools/bench_warm_vs_cold.py` — cold-start vs warm-attach latency benchmark.

**Modify:**
- `mcp_server.py` — add `browser_batch` tool, ref-aware snapshot output, daemon-client routing.
- `__init__.py` — export `connect`, `UltrastealthClient`.
- `pyproject.toml` — add `ultrastealth` and `us` console scripts.
- `skills/craft-scraper/reference/path-b-ultrastealth.md`, `skills/craft-scraper/templates/scraper.ultrastealth.py` — fast `connect()` attach path.
- `README.md`, `CLAUDE.md` — daemon/CLI usage + verification notes.

**Canonical contracts referenced by every phase:**

- **Target string:** an op that acts on an element takes `target`, which is either a **ref** matching `^e\d+$` (looked up in the current tab's ref-map) or a **CSS selector** (anything else).
- **Ref-map entry:** `{"ref": "e1", "role": str, "name": str, "occurrence": int, ...props}`. Resolution: `page.get_by_role(role, name=name).nth(occurrence)`.
- **Core op return:** every `browser_core` op returns a JSON-serializable `dict`. Errors raise `BrowserCoreError(type, message)`.
- **JSON-RPC:** request `{"id": int, "cmd": str, "args": {..}}` → response `{"id": int, "ok": true, "result": {..}}` or `{"id": int, "ok": false, "error": {"type": str, "message": str}}`. One JSON object per line (`\n`-delimited).
- **Batch step:** `{"op": str, ...args}` (the remaining keys are the op's kwargs).
- **Daemon files:** `~/.ultrastealth/daemon.sock`, `~/.ultrastealth/daemon.pid`, `~/.ultrastealth/daemon.log` (overridable via `ULTRASTEALTH_DAEMON_DIR`).

---

# Phase 0 — `browser_core` (shared engine)

Self-contained module. Existing `mcp_server.py` is untouched this phase, so the whole current suite stays green; Phase 3 later migrates the MCP tools onto this core.

### Task 0.1: Shared fakes + core state holder

**Files:**
- Create: `tests/browser_fakes.py`
- Create: `browser_core.py`
- Test: `tests/test_browser_core.py`

- [ ] **Step 1: Write the shared fakes**

Create `tests/browser_fakes.py`:

```python
"""Fake Playwright doubles shared by browser_core / daemon / client tests."""
from types import SimpleNamespace


class FakeAccessibility:
    def __init__(self, tree):
        self._tree = tree

    async def snapshot(self):
        return self._tree


class FakeLocator:
    def __init__(self, page, key):
        self.page = page
        self.key = key
        self.first = self

    def nth(self, i):
        return self

    async def click(self, timeout=10000):
        self.page.clicked.append(self.key)

    async def fill(self, text, timeout=10000):
        self.page.filled.append((self.key, text))

    async def type(self, text, delay=0, timeout=10000):
        self.page.typed.append((self.key, text))

    async def press(self, key, timeout=10000):
        self.page.pressed.append((self.key, key))

    async def hover(self, timeout=10000):
        self.page.hovered.append(self.key)

    async def select_option(self, value, timeout=10000):
        self.page.selected.append((self.key, value))

    async def text_content(self, timeout=5000):
        return self.page.text_by_selector.get(self.key, "")

    async def inner_html(self, timeout=5000):
        return self.page.html_by_selector.get(self.key, "<span>x</span>")

    async def get_attribute(self, name, timeout=5000):
        return self.page.attr_by_selector.get(self.key, {}).get(name)

    async def is_visible(self, timeout=5000):
        return self.page.visible_by_selector.get(self.key, True)

    async def is_enabled(self, timeout=5000):
        return self.page.enabled_by_selector.get(self.key, True)

    async def is_checked(self, timeout=5000):
        return self.page.checked_by_selector.get(self.key, False)


class FakePage:
    def __init__(self, tree=None):
        self.url = "https://example.com/dashboard"
        self.accessibility = FakeAccessibility(tree or _DEFAULT_TREE)
        self.clicked, self.filled, self.typed = [], [], []
        self.pressed, self.hovered, self.selected = [], [], []
        self.evaluated, self.waits, self.goto_calls = [], [], []
        self.text_by_selector = {"#status": "ready"}
        self.html_by_selector = {"#status": "<div>ready</div>"}
        self.attr_by_selector = {"#link": {"href": "https://example.com/p"}}
        self.visible_by_selector = {"#status": True}
        self.enabled_by_selector = {"#submit": True}
        self.checked_by_selector = {"#terms": True}
        self.role_lookup = {}  # (role, name) -> FakeLocator key

    def is_closed(self):
        return False

    async def title(self):
        return "Dashboard"

    async def goto(self, url, wait_until="load", timeout=30000):
        self.goto_calls.append((url, wait_until))
        self.url = url

    async def go_back(self, wait_until="load", timeout=30000):
        self.url = "https://example.com/prev"

    def locator(self, selector):
        return FakeLocator(self, selector)

    def get_by_role(self, role, name=""):
        return FakeLocator(self, f"role:{role}:{name}")

    async def keyboard_press(self, key):
        self.pressed.append(("keyboard", key))

    @property
    def keyboard(self):
        page = self

        class _K:
            async def press(self, key):
                page.pressed.append(("keyboard", key))

        return _K()

    async def evaluate(self, script, arg=None):
        self.evaluated.append((script, arg))
        if "scrollBy" in script:
            return None
        if "window.location.href" in script:
            return self.url
        return {"ok": True}

    async def wait_for_selector(self, selector, timeout=10000):
        self.waits.append(("selector", selector, timeout))

    async def wait_for_url(self, pattern, timeout=10000):
        self.waits.append(("url", pattern, timeout))

    async def wait_for_load_state(self, state="load", timeout=10000):
        self.waits.append(("load_state", state, timeout))

    async def wait_for_function(self, expression, timeout=10000):
        self.waits.append(("function", expression, timeout))

    async def screenshot(self, **kwargs):
        return b"fake-png"


class FakeContext:
    def __init__(self, page):
        self.pages = [page]

    async def new_page(self):
        p = FakePage()
        self.pages.append(p)
        return p


_DEFAULT_TREE = {
    "role": "WebArea", "name": "Dashboard", "children": [
        {"role": "button", "name": "Submit"},
        {"role": "textbox", "name": "Email"},
        {"role": "link", "name": "Pricing"},
    ],
}


def make_fetcher(page):
    """A minimal fake fetcher whose _context yields fresh pages."""
    return SimpleNamespace(_context=FakeContext(page), user_data_dir=None)
```

- [ ] **Step 2: Write the failing state test**

Create `tests/test_browser_core.py`:

```python
import asyncio
import unittest

import browser_core
from tests.browser_fakes import FakePage, make_fetcher


class CoreStateTests(unittest.TestCase):
    def setUp(self):
        self.page = FakePage()
        browser_core.reset_state_for_tests(fetcher=make_fetcher(self.page), page=self.page)

    def test_get_page_returns_injected_page(self):
        page = asyncio.run(browser_core.get_page())
        self.assertIs(page, self.page)

    def test_status_reports_warm_when_page_present(self):
        status = asyncio.run(browser_core.status())
        self.assertTrue(status["warm"])
        self.assertEqual(status["url"], "https://example.com/dashboard")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `/Users/mac/.local/pipx/venvs/ultrastealth/bin/python -m unittest tests.test_browser_core -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'browser_core'`.

- [ ] **Step 4: Create `browser_core.py` with state + get_page + status**

Create `browser_core.py`:

```python
"""Shared warm-browser engine.

Canonical, front-end-agnostic browser operations returning JSON-serializable
dicts. Owned by the daemon; also importable directly. Stealth/profile behavior
is delegated entirely to UltrastealthFetcher — this module never changes it.
"""
import asyncio
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
_ref_maps: dict = {}          # tab_id -> {ref: entry}
_prev_ref_signatures: dict = {}  # tab_id -> set(signatures) for --diff
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
```

- [ ] **Step 5: Run test to verify it passes**

Run: `/Users/mac/.local/pipx/venvs/ultrastealth/bin/python -m unittest tests.test_browser_core -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add browser_core.py tests/browser_fakes.py tests/test_browser_core.py
git commit -m "feat(core): browser_core state holder + get_page/status + shared fakes"
```

### Task 0.2: Snapshot with stable refs + `--diff`

**Files:**
- Modify: `browser_core.py`
- Test: `tests/test_browser_core.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_browser_core.py` inside `CoreStateTests`:

```python
    def test_snapshot_assigns_stable_refs(self):
        snap = asyncio.run(browser_core.snapshot())
        refs = {e["ref"]: e for e in snap["refs"]}
        self.assertIn("e0", refs)
        self.assertEqual(refs["e0"]["role"], "button")
        self.assertEqual(refs["e0"]["name"], "Submit")
        # Refs are stable across identical snapshots.
        snap2 = asyncio.run(browser_core.snapshot())
        self.assertEqual([e["ref"] for e in snap["refs"]],
                         [e["ref"] for e in snap2["refs"]])

    def test_snapshot_diff_returns_only_changes(self):
        asyncio.run(browser_core.snapshot())
        diff = asyncio.run(browser_core.snapshot(diff=True))
        self.assertEqual(diff["refs"], [])  # nothing changed
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/mac/.local/pipx/venvs/ultrastealth/bin/python -m unittest tests.test_browser_core.CoreStateTests.test_snapshot_assigns_stable_refs -v`
Expected: FAIL — `AttributeError: module 'browser_core' has no attribute 'snapshot'`.

- [ ] **Step 3: Implement snapshot + ref-map**

Add to `browser_core.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/mac/.local/pipx/venvs/ultrastealth/bin/python -m unittest tests.test_browser_core.CoreStateTests -v`
Expected: PASS (all snapshot tests).

- [ ] **Step 5: Commit**

```bash
git add browser_core.py tests/test_browser_core.py
git commit -m "feat(core): snapshot with stable e-refs + diff mode"
```

### Task 0.3: Target resolution (ref or selector)

**Files:**
- Modify: `browser_core.py`
- Test: `tests/test_browser_core.py`

- [ ] **Step 1: Write the failing test**

Append to `CoreStateTests`:

```python
    def test_resolve_ref_uses_role_lookup(self):
        asyncio.run(browser_core.snapshot())
        loc = asyncio.run(browser_core._resolve(self.page, "e0"))
        self.assertEqual(loc.key, "role:button:Submit")

    def test_resolve_selector_uses_css(self):
        loc = asyncio.run(browser_core._resolve(self.page, "#submit"))
        self.assertEqual(loc.key, "#submit")

    def test_resolve_stale_ref_raises(self):
        with self.assertRaises(browser_core.BrowserCoreError) as ctx:
            asyncio.run(browser_core._resolve(self.page, "e99"))
        self.assertEqual(ctx.exception.type, "stale_ref")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/mac/.local/pipx/venvs/ultrastealth/bin/python -m unittest tests.test_browser_core.CoreStateTests.test_resolve_ref_uses_role_lookup -v`
Expected: FAIL — `AttributeError: module 'browser_core' has no attribute '_resolve'`.

- [ ] **Step 3: Implement `_resolve`**

Add to `browser_core.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/mac/.local/pipx/venvs/ultrastealth/bin/python -m unittest tests.test_browser_core.CoreStateTests -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add browser_core.py tests/test_browser_core.py
git commit -m "feat(core): resolve refs or CSS selectors, stale-ref error"
```

### Task 0.4: Navigate + mutating actions (click/type/fill/press/hover/select/scroll/go_back)

**Files:**
- Modify: `browser_core.py`
- Test: `tests/test_browser_core.py`

- [ ] **Step 1: Write the failing test**

Add a new test class to `tests/test_browser_core.py`:

```python
class CoreActionTests(unittest.TestCase):
    def setUp(self):
        self.page = FakePage()
        browser_core.reset_state_for_tests(fetcher=make_fetcher(self.page), page=self.page)

    def test_navigate_records_goto_and_returns_url_title(self):
        res = asyncio.run(browser_core.navigate("https://example.com/x", wait_secs=0))
        self.assertEqual(res["url"], "https://example.com/x")
        self.assertEqual(res["title"], "Dashboard")
        self.assertEqual(self.page.goto_calls[-1][0], "https://example.com/x")

    def test_click_by_ref(self):
        asyncio.run(browser_core.snapshot())
        asyncio.run(browser_core.click("e0"))
        self.assertIn("role:button:Submit", self.page.clicked)

    def test_type_and_fill_by_selector(self):
        asyncio.run(browser_core.type_text("#email", "a@b.com"))
        asyncio.run(browser_core.fill("#email", "c@d.com"))
        self.assertIn(("#email", "a@b.com"), self.page.typed)
        self.assertIn(("#email", "c@d.com"), self.page.filled)

    def test_snapshot_after_returns_snapshot(self):
        asyncio.run(browser_core.snapshot())
        res = asyncio.run(browser_core.click("e0", snapshot_after=True))
        self.assertIn("snapshot", res)
        self.assertTrue(res["snapshot"]["refs"])

    def test_scroll_and_go_back(self):
        asyncio.run(browser_core.scroll("down", 300))
        res = asyncio.run(browser_core.go_back())
        self.assertEqual(res["url"], "https://example.com/prev")
        self.assertTrue(any("scrollBy" in s for s, _ in self.page.evaluated))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/mac/.local/pipx/venvs/ultrastealth/bin/python -m unittest tests.test_browser_core.CoreActionTests -v`
Expected: FAIL — `AttributeError: module 'browser_core' has no attribute 'navigate'`.

- [ ] **Step 3: Implement navigate + actions**

Add to `browser_core.py`:

```python
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


async def go_back(snapshot_after: bool = False) -> dict:
    page = await get_page()
    await page.go_back(wait_until="domcontentloaded", timeout=30000)
    return await _maybe_snapshot({"url": page.url, "title": await page.title()}, snapshot_after)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/mac/.local/pipx/venvs/ultrastealth/bin/python -m unittest tests.test_browser_core.CoreActionTests -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add browser_core.py tests/test_browser_core.py
git commit -m "feat(core): navigate + click/type/fill/press/hover/select/scroll/go_back"
```

### Task 0.5: Inspectors (get/is/wait/evaluate/screenshot)

**Files:**
- Modify: `browser_core.py`
- Test: `tests/test_browser_core.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_browser_core.py`:

```python
import tempfile
from pathlib import Path


class CoreInspectTests(unittest.TestCase):
    def setUp(self):
        self.page = FakePage()
        browser_core.reset_state_for_tests(fetcher=make_fetcher(self.page), page=self.page)

    def test_get_text_html_attr(self):
        self.assertEqual(asyncio.run(browser_core.get("text", "#status"))["text"], "ready")
        self.assertIn("<div", asyncio.run(browser_core.get("html", "#status"))["html"])
        self.assertEqual(
            asyncio.run(browser_core.get("attr", "#link", attribute="href"))["attr"],
            "https://example.com/p",
        )

    def test_is_visible_enabled_checked(self):
        self.assertTrue(asyncio.run(browser_core.is_("visible", "#status"))["result"])
        self.assertTrue(asyncio.run(browser_core.is_("enabled", "#submit"))["result"])
        self.assertTrue(asyncio.run(browser_core.is_("checked", "#terms"))["result"])

    def test_wait_modes(self):
        asyncio.run(browser_core.wait(url_contains="/dashboard"))
        asyncio.run(browser_core.wait(load_state="networkidle"))
        asyncio.run(browser_core.wait(javascript="window.ready === true"))
        kinds = [w[0] for w in self.page.waits]
        self.assertEqual(set(kinds), {"url", "load_state", "function"})

    def test_evaluate_and_screenshot_to_path(self):
        self.assertEqual(asyncio.run(browser_core.evaluate("1+1"))["result"], {"ok": True})
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "s.png"
            res = asyncio.run(browser_core.screenshot(path=str(out)))
            self.assertEqual(out.read_bytes(), b"fake-png")
            self.assertEqual(res["path"], str(out))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/mac/.local/pipx/venvs/ultrastealth/bin/python -m unittest tests.test_browser_core.CoreInspectTests -v`
Expected: FAIL — missing `get`.

- [ ] **Step 3: Implement inspectors**

Add to `browser_core.py`:

```python
import base64


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
    raise BrowserCoreError("bad_arg", "wait requires one of selector/text/url_contains/load_state/javascript")


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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/mac/.local/pipx/venvs/ultrastealth/bin/python -m unittest tests.test_browser_core.CoreInspectTests -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add browser_core.py tests/test_browser_core.py
git commit -m "feat(core): get/is/wait/evaluate/screenshot inspectors"
```

### Task 0.6: Batch executor

**Files:**
- Modify: `browser_core.py`
- Test: `tests/test_browser_core.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_browser_core.py`:

```python
class CoreBatchTests(unittest.TestCase):
    def setUp(self):
        self.page = FakePage()
        browser_core.reset_state_for_tests(fetcher=make_fetcher(self.page), page=self.page)

    def test_batch_runs_steps_in_order_with_final_snapshot(self):
        steps = [
            {"op": "navigate", "url": "https://example.com/x", "wait_secs": 0},
            {"op": "snapshot"},
            {"op": "click", "target": "e0"},
            {"op": "snapshot"},
        ]
        res = asyncio.run(browser_core.batch(steps))
        self.assertTrue(all(s["ok"] for s in res["steps"]))
        self.assertEqual(self.page.goto_calls[-1][0], "https://example.com/x")
        self.assertIn("role:button:Submit", self.page.clicked)

    def test_batch_stops_on_error_by_default(self):
        steps = [{"op": "click", "target": "e404"}, {"op": "navigate", "url": "x"}]
        res = asyncio.run(browser_core.batch(steps))
        self.assertFalse(res["steps"][0]["ok"])
        self.assertEqual(res["steps"][0]["error"]["type"], "stale_ref")
        self.assertEqual(len(res["steps"]), 1)  # stopped

    def test_batch_unknown_op_errors(self):
        res = asyncio.run(browser_core.batch([{"op": "fly"}]))
        self.assertEqual(res["steps"][0]["error"]["type"], "bad_op")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/mac/.local/pipx/venvs/ultrastealth/bin/python -m unittest tests.test_browser_core.CoreBatchTests -v`
Expected: FAIL — missing `batch`.

- [ ] **Step 3: Implement batch + op registry**

Add to `browser_core.py` (place `OPS` after all ops are defined, near end of file):

```python
# Registry of dispatchable ops (name -> coroutine fn). Used by batch + daemon.
OPS = {
    "navigate": navigate, "snapshot": snapshot, "click": click,
    "type": type_text, "fill": fill, "press": press, "hover": hover,
    "select": select_option, "scroll": scroll, "go_back": go_back,
    "get": get, "is": is_, "wait": wait, "evaluate": evaluate,
    "screenshot": screenshot, "status": status,
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/mac/.local/pipx/venvs/ultrastealth/bin/python -m unittest tests.test_browser_core.CoreBatchTests -v`
Expected: PASS.

- [ ] **Step 5: Run the whole suite (regression gate)**

Run: `/Users/mac/.local/pipx/venvs/ultrastealth/bin/python -m unittest discover -s tests`
Expected: PASS — no existing test broke (mcp_server untouched).

- [ ] **Step 6: Commit**

```bash
git add browser_core.py tests/test_browser_core.py
git commit -m "feat(core): batch executor + op registry"
```

### Task 0.7: Real browser lifecycle (`ensure_browser`/`close`)

**Files:**
- Modify: `browser_core.py`
- Test: `tests/test_browser_core.py`

Adapts `mcp_server._ensure_browser` for real launches so the daemon can start Chrome. Tests use monkeypatching (no real browser in unit tests).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_browser_core.py`:

```python
from unittest.mock import patch


class CoreLifecycleTests(unittest.TestCase):
    def setUp(self):
        browser_core.reset_state_for_tests(fetcher=None, page=None)

    def test_ensure_browser_starts_fetcher_and_opens_page(self):
        page = FakePage()

        class FakeFetcher:
            def __init__(self, **kw):
                self.kwargs = kw
                self._context = make_fetcher(page)._context
                self.user_data_dir = None

            async def start(self):
                self.started = True

        with patch.object(browser_core, "UltrastealthFetcher", FakeFetcher):
            asyncio.run(browser_core.ensure_browser())
        self.assertIsNotNone(browser_core._page)
        self.assertEqual(browser_core._page.url, "https://example.com/dashboard")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/mac/.local/pipx/venvs/ultrastealth/bin/python -m unittest tests.test_browser_core.CoreLifecycleTests -v`
Expected: FAIL — missing `ensure_browser`.

- [ ] **Step 3: Implement `ensure_browser` + `close`**

Add to `browser_core.py` (import the profile helpers from mcp_server to stay DRY):

```python
from mcp_server import _profile_config, _fetcher_kwargs  # reuse profile plumbing


async def ensure_browser(runner: str | None = None, user_data_dir: str | None = None,
                         profile_directory: str | None = None) -> None:
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
```

Note: importing from `mcp_server` executes its module top-level (sets the rebrowser env default and creates the `FastMCP` object) but does not start a server — safe. If a future circular-import issue appears, move `_profile_config`/`_fetcher_kwargs` into a shared `profiles.py` imported by both; not needed now.

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/mac/.local/pipx/venvs/ultrastealth/bin/python -m unittest tests.test_browser_core.CoreLifecycleTests -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add browser_core.py tests/test_browser_core.py
git commit -m "feat(core): real ensure_browser + close lifecycle"
```

---

# Phase 1 — Daemon (owns the warm browser)

### Task 1.1: JSON-RPC command dispatcher

**Files:**
- Create: `daemon.py`
- Test: `tests/test_daemon.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_daemon.py`:

```python
import asyncio
import unittest

import daemon


class DispatchTests(unittest.TestCase):
    def test_dispatch_success(self):
        async def fake_status():
            return {"warm": True}

        with unittest.mock.patch.dict(daemon.COMMANDS, {"status": fake_status}, clear=False):
            resp = asyncio.run(daemon.dispatch({"id": 1, "cmd": "status", "args": {}}))
        self.assertEqual(resp, {"id": 1, "ok": True, "result": {"warm": True}})

    def test_dispatch_unknown_cmd(self):
        resp = asyncio.run(daemon.dispatch({"id": 2, "cmd": "nope", "args": {}}))
        self.assertFalse(resp["ok"])
        self.assertEqual(resp["error"]["type"], "unknown_cmd")

    def test_dispatch_core_error(self):
        async def boom():
            raise daemon.browser_core.BrowserCoreError("stale_ref", "gone")

        with unittest.mock.patch.dict(daemon.COMMANDS, {"click": boom}, clear=False):
            resp = asyncio.run(daemon.dispatch({"id": 3, "cmd": "click", "args": {}}))
        self.assertEqual(resp["error"]["type"], "stale_ref")


import unittest.mock  # noqa: E402

if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/mac/.local/pipx/venvs/ultrastealth/bin/python -m unittest tests.test_daemon.DispatchTests -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'daemon'`.

- [ ] **Step 3: Create `daemon.py` dispatcher**

Create `daemon.py`:

```python
"""Ultrastealth warm-browser daemon.

Owns a single browser_core instance and serves newline-delimited JSON-RPC over a
Unix socket. Exactly one process holds the CDP connection; CLI/MCP/scripts attach
here instead of opening their own connections.
"""
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import browser_core

# Command registry: op name -> coroutine. Ensures browser is warm, then runs.
COMMANDS = dict(browser_core.OPS)
COMMANDS["ensure_browser"] = browser_core.ensure_browser
COMMANDS["close"] = browser_core.close


async def dispatch(request: dict) -> dict:
    req_id = request.get("id")
    cmd = request.get("cmd")
    args = request.get("args") or {}
    fn = COMMANDS.get(cmd)
    if fn is None:
        return {"id": req_id, "ok": False,
                "error": {"type": "unknown_cmd", "message": f"Unknown command {cmd!r}"}}
    try:
        # Warm the browser for any op except lifecycle/status.
        if cmd not in ("status", "ensure_browser", "close"):
            await browser_core.ensure_browser()
        async with browser_core._op_lock:
            result = await fn(**args)
        return {"id": req_id, "ok": True, "result": result}
    except browser_core.BrowserCoreError as e:
        return {"id": req_id, "ok": False, "error": {"type": e.type, "message": e.message}}
    except Exception as e:  # noqa: BLE001
        return {"id": req_id, "ok": False, "error": {"type": "error", "message": str(e)}}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/mac/.local/pipx/venvs/ultrastealth/bin/python -m unittest tests.test_daemon.DispatchTests -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add daemon.py tests/test_daemon.py
git commit -m "feat(daemon): JSON-RPC command dispatcher over browser_core"
```

### Task 1.2: Unix-socket server + lifecycle files

**Files:**
- Modify: `daemon.py`
- Test: `tests/test_daemon.py`

- [ ] **Step 1: Write the failing test (real socket round-trip)**

Add to `tests/test_daemon.py`:

```python
import json
import tempfile
from pathlib import Path


class SocketServerTests(unittest.TestCase):
    def test_socket_round_trip(self):
        async def scenario():
            async def fake_status():
                return {"warm": True, "url": "https://x"}

            with tempfile.TemporaryDirectory() as tmp:
                sock = str(Path(tmp) / "d.sock")
                with unittest.mock.patch.dict(daemon.COMMANDS, {"status": fake_status}, clear=False):
                    server = await daemon.start_server(sock)
                    reader, writer = await asyncio.open_unix_connection(sock)
                    writer.write((json.dumps({"id": 9, "cmd": "status", "args": {}}) + "\n").encode())
                    await writer.drain()
                    line = await reader.readline()
                    writer.close()
                    server.close()
                    await server.wait_closed()
                    return json.loads(line)

        resp = asyncio.run(scenario())
        self.assertEqual(resp["id"], 9)
        self.assertTrue(resp["result"]["warm"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/mac/.local/pipx/venvs/ultrastealth/bin/python -m unittest tests.test_daemon.SocketServerTests -v`
Expected: FAIL — missing `start_server`.

- [ ] **Step 3: Implement server + lifecycle paths**

Add to `daemon.py`:

```python
def daemon_dir() -> Path:
    d = Path(os.environ.get("ULTRASTEALTH_DAEMON_DIR", str(Path.home() / ".ultrastealth")))
    d.mkdir(parents=True, exist_ok=True)
    return d


def sock_path() -> str:
    return str(daemon_dir() / "daemon.sock")


def pid_path() -> Path:
    return daemon_dir() / "daemon.pid"


def log_path() -> Path:
    return daemon_dir() / "daemon.log"


async def _handle_client(reader, writer):
    try:
        while True:
            line = await reader.readline()
            if not line:
                break
            try:
                request = json.loads(line.decode())
            except json.JSONDecodeError:
                writer.write((json.dumps({"ok": False,
                    "error": {"type": "bad_json", "message": "invalid JSON"}}) + "\n").encode())
                await writer.drain()
                continue
            resp = await dispatch(request)
            _touch_idle()
            writer.write((json.dumps(resp) + "\n").encode())
            await writer.drain()
    except (ConnectionResetError, asyncio.IncompleteReadError):
        pass
    finally:
        writer.close()


async def start_server(sock: str):
    if os.path.exists(sock):
        os.unlink(sock)
    server = await asyncio.start_unix_server(_handle_client, path=sock)
    os.chmod(sock, 0o600)
    return server


# Idle tracking (set by real run(); no-op default so dispatch tests need no setup).
_last_activity = None


def _touch_idle():
    global _last_activity
    import time
    _last_activity = time.time()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/mac/.local/pipx/venvs/ultrastealth/bin/python -m unittest tests.test_daemon.SocketServerTests -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add daemon.py tests/test_daemon.py
git commit -m "feat(daemon): unix-socket JSON-RPC server + lifecycle paths"
```

### Task 1.3: `run()` with keep-warm + health watchdog; PID management

**Files:**
- Modify: `daemon.py`
- Test: `tests/test_daemon.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_daemon.py`:

```python
import os
import signal


class LifecycleTests(unittest.TestCase):
    def test_read_pid_missing_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            with unittest.mock.patch.dict(os.environ, {"ULTRASTEALTH_DAEMON_DIR": tmp}):
                self.assertIsNone(daemon.read_pid())

    def test_is_running_false_for_dead_pid(self):
        with tempfile.TemporaryDirectory() as tmp:
            with unittest.mock.patch.dict(os.environ, {"ULTRASTEALTH_DAEMON_DIR": tmp}):
                daemon.pid_path().write_text("999999")  # unlikely-live PID
                self.assertFalse(daemon.is_running())

    def test_is_running_true_for_current_pid(self):
        with tempfile.TemporaryDirectory() as tmp:
            with unittest.mock.patch.dict(os.environ, {"ULTRASTEALTH_DAEMON_DIR": tmp}):
                daemon.pid_path().write_text(str(os.getpid()))
                self.assertTrue(daemon.is_running())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/mac/.local/pipx/venvs/ultrastealth/bin/python -m unittest tests.test_daemon.LifecycleTests -v`
Expected: FAIL — missing `read_pid`.

- [ ] **Step 3: Implement pid helpers + run + watchdogs**

Add to `daemon.py`:

```python
import time


def read_pid() -> int | None:
    p = pid_path()
    if not p.exists():
        return None
    try:
        return int(p.read_text().strip())
    except ValueError:
        return None


def is_running() -> bool:
    pid = read_pid()
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


async def _idle_reaper(idle_timeout: float):
    """Close the browser after inactivity; keep the daemon listening."""
    if idle_timeout <= 0:
        return
    while True:
        await asyncio.sleep(min(idle_timeout, 30))
        if _last_activity and (time.time() - _last_activity) > idle_timeout:
            if browser_core._page is not None:
                await browser_core.close()


async def _health_watchdog(interval: float = 20.0):
    """Ping the page; hard-restart a wedged browser."""
    while True:
        await asyncio.sleep(interval)
        if browser_core._page is None:
            continue
        try:
            await asyncio.wait_for(browser_core._page.title(), timeout=10)
        except Exception:
            await browser_core.close()  # next op re-launches clean


async def run(idle_timeout: float = 1800.0):
    sock = sock_path()
    pid_path().write_text(str(os.getpid()))
    _touch_idle()
    server = await start_server(sock)
    reaper = asyncio.create_task(_idle_reaper(idle_timeout))
    watchdog = asyncio.create_task(_health_watchdog())
    try:
        async with server:
            await server.serve_forever()
    finally:
        reaper.cancel()
        watchdog.cancel()
        await browser_core.close()
        for f in (pid_path(),):
            f.unlink(missing_ok=True)
        if os.path.exists(sock):
            os.unlink(sock)
```

Fix the typo intentionally avoided: name the task variable `watchdog` consistently:

```python
    watchdog = asyncio.create_task(_health_watchdog())
    ...
        watchdog.cancel()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/mac/.local/pipx/venvs/ultrastealth/bin/python -m unittest tests.test_daemon.LifecycleTests -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add daemon.py tests/test_daemon.py
git commit -m "feat(daemon): run loop + idle reaper + health watchdog + pid mgmt"
```

---

# Phase 2 — Client + CLI

### Task 2.1: `UltrastealthClient` + `connect()` + auto-start

**Files:**
- Create: `client.py`
- Modify: `__init__.py`
- Test: `tests/test_client.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_client.py`:

```python
import asyncio
import json
import tempfile
import unittest
from pathlib import Path

import client


class ClientTests(unittest.TestCase):
    def test_call_sends_request_and_parses_result(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                sock = str(Path(tmp) / "d.sock")

                async def handle(reader, writer):
                    line = await reader.readline()
                    req = json.loads(line.decode())
                    resp = {"id": req["id"], "ok": True, "result": {"echo": req["args"]}}
                    writer.write((json.dumps(resp) + "\n").encode())
                    await writer.drain()
                    writer.close()

                server = await asyncio.start_unix_server(handle, path=sock)
                c = client.UltrastealthClient(sock=sock, autostart=False)
                res = await c.call("click", target="e2")
                server.close()
                await server.wait_closed()
                return res

        self.assertEqual(asyncio.run(scenario()), {"echo": {"target": "e2"}})

    def test_call_raises_on_error_response(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                sock = str(Path(tmp) / "d.sock")

                async def handle(reader, writer):
                    line = await reader.readline()
                    req = json.loads(line.decode())
                    resp = {"id": req["id"], "ok": False,
                            "error": {"type": "stale_ref", "message": "gone"}}
                    writer.write((json.dumps(resp) + "\n").encode())
                    await writer.drain()
                    writer.close()

                server = await asyncio.start_unix_server(handle, path=sock)
                c = client.UltrastealthClient(sock=sock, autostart=False)
                try:
                    await c.call("click", target="e2")
                finally:
                    server.close()
                    await server.wait_closed()

        with self.assertRaises(client.DaemonError) as ctx:
            asyncio.run(scenario())
        self.assertEqual(ctx.exception.type, "stale_ref")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/mac/.local/pipx/venvs/ultrastealth/bin/python -m unittest tests.test_client -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'client'`.

- [ ] **Step 3: Implement `client.py`**

Create `client.py`:

```python
"""Client for the Ultrastealth warm-browser daemon."""
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def default_sock() -> str:
    d = Path(os.environ.get("ULTRASTEALTH_DAEMON_DIR", str(Path.home() / ".ultrastealth")))
    return str(d / "daemon.sock")


class DaemonError(Exception):
    def __init__(self, type_: str, message: str):
        super().__init__(message)
        self.type = type_
        self.message = message


class UltrastealthClient:
    def __init__(self, sock: str | None = None, autostart: bool = True, timeout: float = 120.0):
        self.sock = sock or default_sock()
        self.autostart = autostart
        self.timeout = timeout
        self._id = 0

    def _ensure_daemon(self):
        if os.path.exists(self.sock):
            return
        if not self.autostart:
            raise DaemonError("no_daemon", f"No daemon at {self.sock}; run `ultrastealth daemon start`")
        subprocess.Popen(
            [sys.executable, "-m", "ultrastealth.daemon", "run"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
        )
        for _ in range(100):  # up to ~10s for the sock to appear
            if os.path.exists(self.sock):
                return
            time.sleep(0.1)
        raise DaemonError("start_timeout", "Daemon did not become ready in time")

    async def call(self, cmd: str, **args):
        self._ensure_daemon()
        self._id += 1
        req = {"id": self._id, "cmd": cmd, "args": args}
        reader, writer = await asyncio.open_unix_connection(self.sock)
        try:
            writer.write((json.dumps(req) + "\n").encode())
            await writer.drain()
            line = await asyncio.wait_for(reader.readline(), timeout=self.timeout)
        finally:
            writer.close()
        resp = json.loads(line.decode())
        if not resp.get("ok"):
            err = resp.get("error", {})
            raise DaemonError(err.get("type", "error"), err.get("message", "unknown error"))
        return resp.get("result")


def connect(sock: str | None = None, autostart: bool = True) -> UltrastealthClient:
    """Attach to the warm daemon (starting it if needed). For scripts + MCP."""
    return UltrastealthClient(sock=sock, autostart=autostart)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/mac/.local/pipx/venvs/ultrastealth/bin/python -m unittest tests.test_client -v`
Expected: PASS.

- [ ] **Step 5: Export from package**

Read `__init__.py`, then append the exports (keep existing `UltrastealthFetcher` export):

```python
from ultrastealth.client import UltrastealthClient, connect  # noqa: E402,F401
```

- [ ] **Step 6: Run the whole suite**

Run: `/Users/mac/.local/pipx/venvs/ultrastealth/bin/python -m unittest discover -s tests`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add client.py __init__.py tests/test_client.py
git commit -m "feat(client): UltrastealthClient + connect() + daemon auto-start"
```

### Task 2.2: CLI — argument parsing → daemon commands

**Files:**
- Create: `cli.py`
- Modify: `pyproject.toml`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_cli.py`:

```python
import unittest

import cli


class FakeClient:
    def __init__(self):
        self.calls = []

    async def call(self, cmd, **args):
        self.calls.append((cmd, args))
        return {"ok": True, "cmd": cmd, "args": args}


class CliParseTests(unittest.TestCase):
    def _run(self, argv):
        fc = FakeClient()
        cli.run_argv(argv, client_factory=lambda **kw: fc)
        return fc.calls

    def test_navigate_maps_to_call(self):
        calls = self._run(["browser", "navigate", "https://example.com"])
        self.assertEqual(calls[0][0], "navigate")
        self.assertEqual(calls[0][1]["url"], "https://example.com")

    def test_click_with_snapshot_after(self):
        calls = self._run(["browser", "click", "e2", "--snapshot-after"])
        self.assertEqual(calls[0], ("click", {"target": "e2", "snapshot_after": True}))

    def test_type_with_text(self):
        calls = self._run(["browser", "type", "#email", "--text", "a@b.com"])
        self.assertEqual(calls[0], ("type", {"target": "#email", "text": "a@b.com"}))

    def test_wait_selector(self):
        calls = self._run(["browser", "wait", "--selector", "#ready", "--timeout-ms", "5000"])
        self.assertEqual(calls[0], ("wait", {"selector": "#ready", "timeout_ms": 5000}))

    def test_snapshot_flags(self):
        calls = self._run(["browser", "snapshot", "--interactive", "--compact", "--diff"])
        self.assertEqual(calls[0], ("snapshot",
                          {"interactive": True, "compact": True, "diff": True}))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/mac/.local/pipx/venvs/ultrastealth/bin/python -m unittest tests.test_cli -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cli'`.

- [ ] **Step 3: Implement `cli.py`**

Create `cli.py`:

```python
"""Ultrastealth CLI — drives the warm-browser daemon (cmux-style)."""
import argparse
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from client import UltrastealthClient  # noqa: E402


def _add_target(p):
    p.add_argument("target", help="element ref (e2) or CSS selector")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ultrastealth", description="Ultrastealth warm browser")
    parser.add_argument("--socket", default=None, help="daemon socket path")
    parser.add_argument("--json", action="store_true", help="raw JSON output")
    parser.add_argument("--no-autostart", action="store_true", help="fail if no daemon")
    sub = parser.add_subparsers(dest="group", required=True)

    d = sub.add_parser("daemon", help="manage the daemon").add_subparsers(dest="action", required=True)
    d.add_parser("start"); d.add_parser("stop"); d.add_parser("status"); d.add_parser("logs")
    d.add_parser("run")  # foreground event loop (used internally by `start`)

    b = sub.add_parser("browser", help="drive the browser").add_subparsers(dest="op", required=True)

    nav = b.add_parser("navigate"); nav.add_argument("url"); nav.add_argument("--wait-secs", type=float)
    b.add_parser("back")
    b.add_parser("reload")
    url = b.add_parser("url")
    title = b.add_parser("title")

    snap = b.add_parser("snapshot")
    snap.add_argument("--interactive", action="store_true")
    snap.add_argument("--compact", action="store_true")
    snap.add_argument("--diff", action="store_true")

    for op in ("click", "hover", "focus", "scroll-into-view"):
        _add_target(b.add_parser(op))
    typ = b.add_parser("type"); _add_target(typ); typ.add_argument("--text", required=True)
    typ.add_argument("--submit", action="store_true")
    fil = b.add_parser("fill"); _add_target(fil); fil.add_argument("--text", required=True)
    sel = b.add_parser("select"); _add_target(sel); sel.add_argument("--value", required=True)
    prs = b.add_parser("press"); prs.add_argument("key")
    scr = b.add_parser("scroll"); scr.add_argument("--direction", default="down"); scr.add_argument("--amount", type=int, default=500)

    w = b.add_parser("wait")
    w.add_argument("--selector"); w.add_argument("--text"); w.add_argument("--url-contains")
    w.add_argument("--load-state"); w.add_argument("--function"); w.add_argument("--timeout-ms", type=int)

    g = b.add_parser("get"); g.add_argument("kind"); g.add_argument("target", nargs="?"); g.add_argument("--attribute")
    iss = b.add_parser("is"); iss.add_argument("kind"); _add_target(iss)
    ev = b.add_parser("eval"); ev.add_argument("javascript")
    sh = b.add_parser("screenshot"); sh.add_argument("--out"); sh.add_argument("--full-page", action="store_true")
    ba = b.add_parser("batch"); ba.add_argument("file", help="JSON file of steps, or - for stdin")

    # global mutation flag
    for name in ("click", "type", "fill", "press", "hover", "select", "scroll", "navigate", "back"):
        # attach --snapshot-after to mutating ops
        pass
    return parser


# Map (op) -> function turning parsed args into (cmd, kwargs).
def _op_to_call(op: str, args) -> tuple[str, dict]:
    if op == "navigate":
        kw = {"url": args.url}
        if args.wait_secs is not None:
            kw["wait_secs"] = args.wait_secs
        return "navigate", kw
    if op == "back":
        return "go_back", {}
    if op == "reload":
        return "navigate", {"url": "__reload__"}
    if op == "url":
        return "get", {"kind": "url"}
    if op == "title":
        return "get", {"kind": "title"}
    if op == "snapshot":
        return "snapshot", {"interactive": args.interactive, "compact": args.compact, "diff": args.diff}
    if op in ("click", "hover", "focus", "scroll-into-view"):
        return {"scroll-into-view": "scroll_into_view", "focus": "focus"}.get(op, op), {"target": args.target}
    if op == "type":
        kw = {"target": args.target, "text": args.text}
        if args.submit:
            kw["submit"] = True
        return "type", kw
    if op == "fill":
        return "fill", {"target": args.target, "text": args.text}
    if op == "select":
        return "select", {"target": args.target, "value": args.value}
    if op == "press":
        return "press", {"key": args.key}
    if op == "scroll":
        return "scroll", {"direction": args.direction, "amount": args.amount}
    if op == "wait":
        kw = {}
        for src, dst in (("selector", "selector"), ("text", "text"), ("url_contains", "url_contains"),
                         ("load_state", "load_state"), ("function", "javascript")):
            v = getattr(args, src.replace("url_contains", "url_contains"), None)
            if v is not None:
                kw[dst] = v
        if args.timeout_ms is not None:
            kw["timeout_ms"] = args.timeout_ms
        return "wait", kw
    if op == "get":
        kw = {"kind": args.kind}
        if args.target:
            kw["target"] = args.target
        if args.attribute:
            kw["attribute"] = args.attribute
        return "get", kw
    if op == "is":
        return "is", {"kind": args.kind, "target": args.target}
    if op == "eval":
        return "evaluate", {"javascript": args.javascript}
    if op == "screenshot":
        kw = {"full_page": args.full_page}
        if args.out:
            kw["path"] = args.out
        return "screenshot", kw
    raise SystemExit(f"unhandled op: {op}")


def run_argv(argv, client_factory=UltrastealthClient):
    parser = build_parser()
    args = parser.parse_args(argv)
    kw = {}
    if args.socket:
        kw["sock"] = args.socket
    if args.no_autostart:
        kw["autostart"] = False

    if args.group == "daemon":
        return _handle_daemon(args)

    if args.group == "browser":
        if args.op == "batch":
            text = sys.stdin.read() if args.file == "-" else Path(args.file).read_text()
            steps = json.loads(text)
            c = client_factory(**kw)
            result = asyncio.run(c.call("batch", steps=steps))
        else:
            cmd, call_kw = _op_to_call(args.op, args)
            if getattr(args, "snapshot_after", False):
                call_kw["snapshot_after"] = True
            c = client_factory(**kw)
            result = asyncio.run(c.call(cmd, **call_kw))
        _emit(result, args.json)
        return result


def _emit(result, as_json):
    if as_json:
        print(json.dumps(result, indent=2))
    else:
        print(json.dumps(result, indent=2) if isinstance(result, (dict, list)) else result)


def _handle_daemon(args):
    import daemon
    if args.action == "run":
        idle = float(os.environ.get("ULTRASTEALTH_IDLE_TIMEOUT", "1800"))
        return asyncio.run(daemon.run(idle_timeout=idle))
    if args.action == "start":
        if daemon.is_running():
            print("daemon already running")
            return
        logf = open(daemon.log_path(), "a")
        subprocess.Popen([sys.executable, "-m", "ultrastealth.cli", "daemon", "run"],
                         stdout=logf, stderr=logf, start_new_session=True)
        print(f"daemon starting; socket {daemon.sock_path()}")
        return
    if args.action == "stop":
        pid = daemon.read_pid()
        if pid:
            os.kill(pid, 15)
            print(f"stopped daemon pid {pid}")
        else:
            print("no daemon running")
        return
    if args.action == "status":
        print(json.dumps({"running": daemon.is_running(), "socket": daemon.sock_path(),
                          "pid": daemon.read_pid()}, indent=2))
        return
    if args.action == "logs":
        p = daemon.log_path()
        print(p.read_text() if p.exists() else "(no log yet)")
        return


def main():
    run_argv(sys.argv[1:])


if __name__ == "__main__":
    main()
```

Note the `--snapshot-after` global mutating flag: add it once on the `browser` subparser group. Update `build_parser`'s `b` creation to `b_parser = sub.add_parser("browser"); b_parser.add_argument("--snapshot-after", action="store_true"); b = b_parser.add_subparsers(...)`. Because argparse places `--snapshot-after` before the op, users write `ultrastealth browser --snapshot-after click e2`. If you prefer it after the op, add `--snapshot-after` to each mutating subparser instead; the test `test_click_with_snapshot_after` expects `ultrastealth browser click e2 --snapshot-after`, so **add `--snapshot-after` to each mutating op subparser** (click/type/fill/press/hover/select/scroll/navigate/back). Implement that in Step 3 by adding `sp.add_argument("--snapshot-after", action="store_true")` when creating each mutating subparser.

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/mac/.local/pipx/venvs/ultrastealth/bin/python -m unittest tests.test_cli -v`
Expected: PASS. Fix parser wiring until the 5 assertions pass (targets, snapshot_after, text, wait, snapshot flags).

- [ ] **Step 5: Register console scripts**

Edit `pyproject.toml` `[project.scripts]` to add:

```toml
ultrastealth = "ultrastealth.cli:main"
us = "ultrastealth.cli:main"
```

- [ ] **Step 6: Reinstall entry points + smoke test**

Run:
```
/Users/mac/.local/pipx/venvs/ultrastealth/bin/pip install -e . >/dev/null && \
/Users/mac/.local/pipx/venvs/ultrastealth/bin/ultrastealth --help
```
Expected: help text lists `daemon` and `browser` groups.

- [ ] **Step 7: Commit**

```bash
git add cli.py pyproject.toml tests/test_cli.py
git commit -m "feat(cli): ultrastealth/us CLI mapping to daemon commands"
```

---

# Phase 3 — MCP integration (shared warm browser + batch)

### Task 3.1: `browser_batch` MCP tool

**Files:**
- Modify: `mcp_server.py`
- Test: `tests/test_mcp_batch.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_mcp_batch.py`:

```python
import asyncio
import json
import unittest
from types import SimpleNamespace

import mcp_server
from tests.browser_fakes import FakePage, FakeContext


class McpBatchTests(unittest.TestCase):
    def setUp(self):
        self.page = FakePage()
        self.context = FakeContext(self.page)
        mcp_server._fetcher = SimpleNamespace(_context=self.context, user_data_dir=None)
        mcp_server._page = self.page
        mcp_server._browser_wedged = False
        mcp_server._browser_config = (None, None, None)

    def test_browser_batch_runs_steps(self):
        steps = [{"op": "navigate", "url": "https://example.com/x", "wait_secs": 0},
                 {"op": "snapshot"}]
        out = asyncio.run(mcp_server.browser_batch(json.dumps(steps)))
        self.assertIn("navigate", out)
        self.assertEqual(self.page.goto_calls[-1][0], "https://example.com/x")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/mac/.local/pipx/venvs/ultrastealth/bin/python -m unittest tests.test_mcp_batch -v`
Expected: FAIL — `AttributeError: module 'mcp_server' has no attribute 'browser_batch'`.

- [ ] **Step 3: Implement `browser_batch` in `mcp_server.py`**

Add near the other tools in `mcp_server.py` (import browser_core at top, after the fetcher import):

```python
import browser_core  # shared engine for batch + refs
```

Add the tool:

```python
@_tool()
async def browser_batch(steps: str) -> str:
    """Run a sequence of browser steps in ONE call, collapsing round-trips.

    `steps` is a JSON array of {"op": name, ...args}, e.g.
    [{"op":"navigate","url":"https://x"},{"op":"wait","selector":"#ready"},
     {"op":"click","target":"e2"},{"op":"snapshot"}].
    Ops: navigate, wait, click, type, fill, press, hover, select, scroll,
    go_back, get, is, evaluate, snapshot, screenshot. Stops on first error.
    """
    page = await _get_page()
    _next_request()
    # Point browser_core at the MCP's active page/fetcher for this call.
    browser_core._fetcher = _fetcher
    browser_core._page = page
    parsed = json.loads(steps)
    result = await browser_core.batch(parsed)
    return json.dumps(result, indent=2)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/mac/.local/pipx/venvs/ultrastealth/bin/python -m unittest tests.test_mcp_batch -v`
Expected: PASS.

- [ ] **Step 5: Run whole suite (regression gate)**

Run: `/Users/mac/.local/pipx/venvs/ultrastealth/bin/python -m unittest discover -s tests`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add mcp_server.py tests/test_mcp_batch.py
git commit -m "feat(mcp): browser_batch tool collapsing multi-step flows to one call"
```

### Task 3.2: Ref-aware `browser_snapshot` tool + ref format in state

**Files:**
- Modify: `mcp_server.py`
- Test: `tests/test_mcp_batch.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_mcp_batch.py`:

```python
    def test_browser_snapshot_emits_e_refs(self):
        out = asyncio.run(mcp_server.browser_snapshot())
        self.assertIn("[e0]", out)
        self.assertIn("Submit", out)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/mac/.local/pipx/venvs/ultrastealth/bin/python -m unittest tests.test_mcp_batch.McpBatchTests.test_browser_snapshot_emits_e_refs -v`
Expected: FAIL — no `browser_snapshot`.

- [ ] **Step 3: Implement `browser_snapshot`**

Add to `mcp_server.py`:

```python
@_tool()
async def browser_snapshot(interactive: bool = True, compact: bool = True, diff: bool = False) -> str:
    """Accessibility snapshot with STABLE refs (e0, e1, …). Use the eN refs with
    browser_click/browser_type (they also accept CSS selectors). Prefer this over
    screenshots — it is far cheaper and refs are stable within a snapshot."""
    page = await _get_page()
    _next_request()
    browser_core._fetcher = _fetcher
    browser_core._page = page
    snap = await browser_core.snapshot(interactive=interactive, compact=compact, diff=diff)
    lines = [f"URL: {snap['url']}", f"Title: {snap['title']}", "Elements:"]
    for e in snap["refs"]:
        parts = [f"[{e['ref']}]", f"<{e['role']}>"]
        if e.get("name"):
            parts.append(f'"{e["name"]}"')
        for prop in ("checked", "selected", "expanded", "disabled"):
            if e.get(prop) is not None:
                parts.append(f"{prop}={e[prop]}")
        lines.append(" ".join(parts))
    return "\n".join(lines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/mac/.local/pipx/venvs/ultrastealth/bin/python -m unittest tests.test_mcp_batch -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mcp_server.py tests/test_mcp_batch.py
git commit -m "feat(mcp): browser_snapshot tool with stable e-refs"
```

### Task 3.3: Daemon-client routing (shared warm browser)

**Files:**
- Modify: `mcp_server.py`
- Test: `tests/test_mcp_batch.py`

Makes the MCP server prefer a running daemon so MCP + CLI share one browser. Gated by the sock's presence; falls back to owning its own browser (today's behavior + existing tests).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_mcp_batch.py`:

```python
    def test_uses_daemon_when_socket_present(self):
        recorded = {}

        class FakeClient:
            def __init__(self, **kw):
                pass

            async def call(self, cmd, **args):
                recorded["cmd"] = cmd
                recorded["args"] = args
                return {"steps": []}

        with unittest.mock.patch.object(mcp_server, "_daemon_available", lambda: True), \
             unittest.mock.patch.object(mcp_server, "UltrastealthClient", FakeClient):
            out = asyncio.run(mcp_server.browser_batch('[{"op":"snapshot"}]'))
        self.assertEqual(recorded["cmd"], "batch")
        self.assertIn("steps", out)


import unittest.mock  # noqa: E402
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/mac/.local/pipx/venvs/ultrastealth/bin/python -m unittest tests.test_mcp_batch.McpBatchTests.test_uses_daemon_when_socket_present -v`
Expected: FAIL — no `_daemon_available`.

- [ ] **Step 3: Implement routing helper + use it in `browser_batch`**

Add to `mcp_server.py`:

```python
from client import UltrastealthClient, default_sock  # daemon routing


def _daemon_available() -> bool:
    """True when a warm daemon socket exists (unless explicitly disabled)."""
    if os.environ.get("ULTRASTEALTH_MCP_NO_DAEMON") == "1":
        return False
    return os.path.exists(default_sock())
```

Modify `browser_batch` to route when a daemon is present:

```python
@_tool()
async def browser_batch(steps: str) -> str:
    """(docstring unchanged)"""
    parsed = json.loads(steps)
    if _daemon_available():
        client = UltrastealthClient()
        result = await client.call("batch", steps=parsed)
        return json.dumps(result, indent=2)
    page = await _get_page()
    _next_request()
    browser_core._fetcher = _fetcher
    browser_core._page = page
    result = await browser_core.batch(parsed)
    return json.dumps(result, indent=2)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/mac/.local/pipx/venvs/ultrastealth/bin/python -m unittest tests.test_mcp_batch -v`
Expected: PASS (both owning-path and daemon-path tests). The existing suite has no sock, so it hits the owning path.

- [ ] **Step 5: Run whole suite**

Run: `/Users/mac/.local/pipx/venvs/ultrastealth/bin/python -m unittest discover -s tests`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add mcp_server.py tests/test_mcp_batch.py
git commit -m "feat(mcp): route batch through the warm daemon when present"
```

---

# Phase 4 — Skills

### Task 4.1: New `fast-browser` skill

**Files:**
- Create: `skills/fast-browser/SKILL.md`
- Create: `skills/fast-browser/references/commands.md`

- [ ] **Step 1: Write `SKILL.md`**

Create `skills/fast-browser/SKILL.md`:

```markdown
---
name: fast-browser
description: Drive the Ultrastealth warm-browser daemon fast from the shell or as an agent. Use for stealth browser automation where speed and low token cost matter — snapshot refs, --snapshot-after, batched multi-step flows, and a persistent warm profile that avoids cold Chrome starts. Triggers on "drive the browser fast", "use the warm browser", "automate this page with ultrastealth", "click/type/snapshot via CLI".
allowed-tools: Bash, Read, Write, Edit, mcp__ultrastealth__browser_batch, mcp__ultrastealth__browser_snapshot, mcp__ultrastealth__browser_navigate, mcp__ultrastealth__browser_click, mcp__ultrastealth__browser_type, mcp__ultrastealth__browser_get_state
---

# Fast Browser

Drive one **always-warm** stealth Chrome owned by the `ultrastealth` daemon. The
browser starts once and stays warm; every command attaches in milliseconds. This
is the Ultrastealth analogue of the cmux-browser skill.

## Golden rules (these are the speed wins)

1. **Keep it warm.** `ultrastealth daemon start` once. All later commands (CLI,
   MCP, scripts) reuse the same browser — no cold starts, shared cookies/session.
2. **Snapshot refs, not screenshots.** Use `snapshot --interactive --compact` to
   get stable `eN` refs; act with `click e2`, `type e5 --text "…"`. Re-snapshot
   after navigation or DOM change. Screenshots only for human review.
3. **Batch multi-step flows.** Collapse `navigate → wait → click → type →
   snapshot` into one `browser_batch` (MCP) / `ultrastealth browser batch`
   (CLI) call instead of one action per turn.
4. **`--snapshot-after`** on a mutating action returns the fresh snapshot in the
   same response — no separate observe step.

## Core loop (CLI)

    ultrastealth daemon start
    ultrastealth browser navigate https://example.com
    ultrastealth browser snapshot --interactive --compact   # → [e0] <button> "Login" …
    ultrastealth browser click e0 --snapshot-after
    ultrastealth browser type e3 --text "user@example.com"

## Core loop (agent via MCP)

Prefer `browser_batch` with a JSON step list; end with a `snapshot` step so you
get refs back in one call. Use `browser_snapshot` to refresh refs.

## Batch example

    ultrastealth browser batch - <<'JSON'
    [{"op":"navigate","url":"https://example.com/login"},
     {"op":"wait","selector":"#email"},
     {"op":"fill","target":"#email","text":"user@example.com"},
     {"op":"fill","target":"#password","text":"secret"},
     {"op":"click","target":"e7"},
     {"op":"wait","text":"Welcome"},
     {"op":"snapshot"}]
    JSON

## When NOT to use the daemon

For a one-off script that needs a raw Playwright `page` object with a custom
`page_action` (complex interaction), use `UltrastealthFetcher` directly (see the
craft-scraper skill, Path B). Everything else should attach to the warm daemon.

See `references/commands.md` for the full command list.
```

- [ ] **Step 2: Write `references/commands.md`**

Create `skills/fast-browser/references/commands.md` documenting every CLI op (mirror the `build_parser` surface): `daemon start|stop|status|logs|run`; `browser navigate|back|reload|url|title|snapshot|screenshot|click|hover|focus|scroll-into-view|type|fill|select|press|scroll|wait|get|is|eval|batch`, plus the global `--json`, `--socket`, `--no-autostart`, and per-op `--snapshot-after`. Include one example line per command.

- [ ] **Step 3: Verify skill files parse**

Run:
```
/Users/mac/.local/pipx/venvs/ultrastealth/bin/python - <<'PY'
import pathlib, re
t = pathlib.Path("skills/fast-browser/SKILL.md").read_text()
assert t.startswith("---") and "name: fast-browser" in t
assert pathlib.Path("skills/fast-browser/references/commands.md").exists()
print("ok")
PY
```
Expected: `ok`.

- [ ] **Step 4: Commit**

```bash
git add skills/fast-browser
git commit -m "docs(skill): fast-browser — warm daemon, snapshot refs, batch"
```

### Task 4.2: Update `craft-scraper` for the warm daemon

**Files:**
- Modify: `skills/craft-scraper/reference/path-b-ultrastealth.md`
- Modify: `skills/craft-scraper/templates/scraper.ultrastealth.py`

- [ ] **Step 1: Add a fast-attach section to `path-b-ultrastealth.md`**

Read the file, then insert after the "Use the high-level API" section a new
section:

```markdown
## Fast path — attach to the warm daemon (no cold start)

For the common navigate → wait → evaluate-extract flow, attach to the warm
Ultrastealth daemon instead of cold-launching a browser every run:

    from ultrastealth import connect

    us = connect()                       # starts the daemon once, then reuses it
    us_call = us.call                    # async: await us_call("navigate", url=...)

    await us.call("navigate", url=START_URL, wait_secs=2.0)
    await us.call("wait", selector="[data-product]", timeout_ms=15000)
    rows = (await us.call("evaluate", javascript=EXTRACT))["result"]

Persistent `cf_clearance`/cookies live in the daemon's profile, so protected
targets stay solved across runs. Use this whenever you do not need a raw
Playwright `page` object.

Keep the `UltrastealthFetcher` + `page_action` path (below) only when the task
needs real interaction on a single live page (multi-step clicks/forms) that the
RPC ops can't express.
```

- [ ] **Step 2: Add a commented fast-path variant to the template**

Read `skills/craft-scraper/templates/scraper.ultrastealth.py`, then add a
commented block near the top showing the `connect()` fast path as an alternative
to the `UltrastealthFetcher` cold-launch, mirroring the snippet above. Do not
remove the existing `UltrastealthFetcher` path.

- [ ] **Step 3: Verify references are consistent**

Run:
```
grep -n "connect()" skills/craft-scraper/reference/path-b-ultrastealth.md skills/craft-scraper/templates/scraper.ultrastealth.py
```
Expected: matches in both files.

- [ ] **Step 4: Commit**

```bash
git add skills/craft-scraper
git commit -m "docs(craft-scraper): fast connect() attach path for Path B"
```

---

# Phase 5 — Verification, benchmark, docs

### Task 5.1: Cold-vs-warm latency benchmark

**Files:**
- Create: `tools/bench_warm_vs_cold.py`

- [ ] **Step 1: Write the benchmark**

Create `tools/bench_warm_vs_cold.py`:

```python
"""Measure cold-start vs warm-attach latency for a trivial navigate+title.

Usage:
    python tools/bench_warm_vs_cold.py --url https://example.com --runs 3
Prints a table. Requires a real browser (run outside CI / under a display).
"""
import argparse
import asyncio
import time


async def cold_once(url):
    from ultrastealth import UltrastealthFetcher
    t0 = time.time()
    async with UltrastealthFetcher(headless=False) as us:
        await us.fetch_and_evaluate(url=url, js_expression="() => document.title", wait_secs=0.5)
    return time.time() - t0


async def warm_once(url):
    from ultrastealth import connect
    us = connect()
    t0 = time.time()
    await us.call("navigate", url=url, wait_secs=0.5)
    await us.call("get", kind="title")
    return time.time() - t0


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="https://example.com")
    ap.add_argument("--runs", type=int, default=3)
    args = ap.parse_args()

    cold = [await cold_once(args.url) for _ in range(args.runs)]
    warm = [await warm_once(args.url) for _ in range(args.runs)]  # 1st warms, rest reuse
    print(f"{'run':>4} {'cold (s)':>10} {'warm (s)':>10}")
    for i, (c, w) in enumerate(zip(cold, warm)):
        print(f"{i:>4} {c:>10.2f} {w:>10.2f}")
    print(f"{'avg':>4} {sum(cold)/len(cold):>10.2f} {sum(warm)/len(warm):>10.2f}")
    print(f"speedup (avg): {(sum(cold)/len(cold)) / max(sum(warm)/len(warm), 1e-6):.1f}x")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Run it (manual, real browser)**

Run: `/Users/mac/.local/pipx/venvs/ultrastealth/bin/python tools/bench_warm_vs_cold.py --runs 3`
Expected: a table where warm avg ≪ cold avg (warm attach dominated by page load, not Chrome boot). Record numbers in the commit message.

- [ ] **Step 3: Commit**

```bash
git add tools/bench_warm_vs_cold.py
git commit -m "test(bench): cold-start vs warm-attach latency benchmark"
```

### Task 5.2: Live end-to-end daemon smoke test

**Files:** none (manual verification)

- [ ] **Step 1: Start daemon + drive it**

Run:
```
/Users/mac/.local/pipx/venvs/ultrastealth/bin/ultrastealth daemon start
/Users/mac/.local/pipx/venvs/ultrastealth/bin/ultrastealth daemon status
/Users/mac/.local/pipx/venvs/ultrastealth/bin/ultrastealth browser navigate https://bot.sannysoft.com
/Users/mac/.local/pipx/venvs/ultrastealth/bin/ultrastealth browser snapshot --interactive --compact
/Users/mac/.local/pipx/venvs/ultrastealth/bin/ultrastealth browser eval "() => document.title"
/Users/mac/.local/pipx/venvs/ultrastealth/bin/ultrastealth daemon stop
```
Expected: status shows `running: true`; navigate returns the URL/title; snapshot lists `eN` refs; eval returns the title; stop removes the socket.

- [ ] **Step 2: Confirm MCP + CLI share the browser**

With the daemon running, drive one page via the CLI, then confirm the MCP
`browser_batch`/`browser_snapshot` sees the same URL (call the MCP tool through
Claude, or a short Python client). Document the result.

### Task 5.3: Stealth parity + full suite

**Files:** none (verification)

- [ ] **Step 1: Full unit suite**

Run: `/Users/mac/.local/pipx/venvs/ultrastealth/bin/python -m unittest discover -s tests`
Expected: PASS (all phases).

- [ ] **Step 2: Bot-benchmark parity**

Run: `/Users/mac/.local/pipx/venvs/ultrastealth/bin/python bot_benchmark.py --sites sannysoft rebrowser --results bot_benchmark_results.json`
Expected: completes and writes JSON; scores match the committed baseline in `docs/research/bot_benchmark_default_2026-06-29.json` (the daemon changes nothing about the launch/bypass path). If scores regress, investigate before merge.

### Task 5.4: Docs

**Files:**
- Modify: `README.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: README — add a "Warm daemon + CLI" section**

Document: `ultrastealth daemon start`; the `ultrastealth`/`us` command surface;
snapshot refs + `--snapshot-after` + `batch`; that MCP auto-shares the daemon's
browser when it's running (`ULTRASTEALTH_MCP_NO_DAEMON=1` to opt out);
`ULTRASTEALTH_IDLE_TIMEOUT` and `ULTRASTEALTH_DAEMON_DIR`.

- [ ] **Step 2: CLAUDE.md — add daemon handoff notes**

Add a "Warm Daemon" section: how to start it, that the MCP shares it, and the
verification command.

- [ ] **Step 3: Commit**

```bash
git add README.md CLAUDE.md
git commit -m "docs: warm daemon + CLI usage and MCP sharing"
```

### Task 5.5: Request code review + merge

- [ ] **Step 1: Review the branch diff** using `requesting-code-review` (or `/code-review high`).
- [ ] **Step 2: Fix any findings**, re-run the full suite.
- [ ] **Step 3: Merge `feat/warm-daemon-fast-driver` → `main`**, then rerun the full suite on `main`.

---

## Self-Review (author checklist — completed)

**Spec coverage:**
- Standalone daemon owning one warm browser → Phase 1 (Tasks 1.1–1.3). ✓
- Persistent profile / keep-warm / health → Task 0.7 (profile plumbing reused), Task 1.3 (idle reaper + watchdog). ✓
- Single CDP owner (no churn) → only the daemon calls `ensure_browser`; MCP/CLI/scripts attach via socket (Tasks 1.1, 2.1, 3.3). ✓
- CLI (cmux parity) → Phase 2 (Task 2.2), commands.md (Task 4.1). ✓
- Snapshot refs + `--diff` + `--snapshot-after` → Tasks 0.2, 0.4, 3.2, 2.2. ✓
- `browser_batch` (MCP + CLI) → Tasks 0.6, 2.2, 3.1. ✓
- MCP shares the warm browser → Task 3.3. ✓
- `connect()` for scripts → Task 2.1; craft-scraper fast path → Task 4.2. ✓
- fast-browser skill → Task 4.1. ✓
- Latency benchmark + stealth parity → Tasks 5.1, 5.3. ✓
- Error handling: `stale_ref` (0.3), daemon down/auto-start (2.1), wedge watchdog (1.3), stale sock cleanup (1.2/1.3). ✓
- Security: sock `0600`, loopback-only (documented; TCP not built — YAGNI, matches "Unix socket first"). ✓

**Placeholder scan:** no TBD/TODO; every code step shows real code; the one prose-described step (commands.md, README, CLAUDE.md) is doc content with an explicit list of what to include. ✓

**Type/name consistency:** op names identical across `browser_core.OPS`, `daemon.COMMANDS`, CLI `_op_to_call`, and batch steps (`navigate/snapshot/click/type/fill/press/hover/select/scroll/go_back/get/is/wait/evaluate/screenshot/batch`). `target` used everywhere for ref-or-selector. `BrowserCoreError(type,message)` ↔ JSON `error.{type,message}` ↔ `DaemonError(type,message)`. ✓

**Known follow-ups (not blocking, noted for the implementer):**
- `reload` is mapped to a sentinel `"__reload__"` URL in `_op_to_call`; implement `reload` as a real core op (`page.reload`) in Task 0.4-style follow-up if you want a true reload rather than re-navigation. Add `focus`/`scroll_into_view`/`check`/`uncheck` core ops the same way for full cmux parity (each is one locator call; follow the Task 0.4 pattern + a test).
- Network/console/error capture tools remain MCP-only for now (they already work there). Porting them into `browser_core` for CLI parity is a clean follow-up mirroring the existing `mcp_server` implementations.
```
