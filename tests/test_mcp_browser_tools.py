import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import mcp_server


class FakeConsoleMessage:
    def __init__(self, msg_type="log", text=""):
        self.type = msg_type
        self.text = text
        self.location = {"url": "https://example.com/app.js", "lineNumber": 7, "columnNumber": 3}


class FakeLocator:
    def __init__(self, page, selector):
        self.page = page
        self.selector = selector
        self.first = self

    async def inner_html(self, timeout=5000):
        return self.page.html_by_selector.get(self.selector, "<span>ready</span>")

    async def text_content(self, timeout=5000):
        return self.page.text_by_selector.get(self.selector, "")

    async def input_value(self, timeout=5000):
        return self.page.value_by_selector.get(self.selector, "")

    async def get_attribute(self, name, timeout=5000):
        return self.page.attr_by_selector.get(self.selector, {}).get(name)

    async def count(self):
        return self.page.count_by_selector.get(self.selector, 1)

    async def bounding_box(self, timeout=5000):
        return self.page.box_by_selector.get(
            self.selector,
            {"x": 10, "y": 20, "width": 30, "height": 40},
        )

    async def evaluate(self, script):
        self.page.evaluated_locator_scripts.append((self.selector, script))
        if "getComputedStyle" in script:
            return {"color": "rgb(255, 0, 0)"}
        return None

    async def is_visible(self, timeout=5000):
        return self.page.visible_by_selector.get(self.selector, True)

    async def is_enabled(self, timeout=5000):
        return self.page.enabled_by_selector.get(self.selector, True)

    async def is_checked(self, timeout=5000):
        return self.page.checked_by_selector.get(self.selector, False)

    async def scroll_into_view_if_needed(self, timeout=5000):
        self.page.scrolled_selectors.append(self.selector)

    async def focus(self, timeout=5000):
        self.page.focused_selectors.append(self.selector)


class FakePage:
    def __init__(self):
        self.url = "https://example.com/dashboard"
        self.handlers = {}
        self.waits = []
        self.added_scripts = []
        self.added_styles = []
        self.evaluated = []
        self.evaluated_locator_scripts = []
        self.scrolled_selectors = []
        self.focused_selectors = []
        self.text_by_selector = {"#status": "ready"}
        self.html_by_selector = {"#status": "<div id='status'>ready</div>"}
        self.value_by_selector = {"#email": "ops@example.com"}
        self.attr_by_selector = {"#link": {"href": "https://example.com/pricing"}}
        self.count_by_selector = {".row": 3}
        self.box_by_selector = {}
        self.visible_by_selector = {"#status": True}
        self.enabled_by_selector = {"#submit": True}
        self.checked_by_selector = {"#terms": True}
        self.local_storage = {}
        self.session_storage = {}

    def is_closed(self):
        return False

    async def title(self):
        return "Dashboard"

    def on(self, event, handler):
        self.handlers[event] = handler

    def remove_listener(self, event, handler):
        if self.handlers.get(event) is handler:
            del self.handlers[event]

    def emit(self, event, payload):
        self.handlers[event](payload)

    def locator(self, selector):
        return FakeLocator(self, selector)

    async def screenshot(self, **kwargs):
        return b"fake-png"

    async def wait_for_selector(self, selector, timeout=10000):
        self.waits.append(("selector", selector, timeout))

    async def wait_for_url(self, pattern, timeout=10000):
        self.waits.append(("url", pattern, timeout))

    async def wait_for_load_state(self, state="load", timeout=10000):
        self.waits.append(("load_state", state, timeout))

    async def wait_for_function(self, expression, timeout=10000):
        self.waits.append(("function", expression, timeout))

    def get_by_text(self, text):
        self.waits.append(("text_lookup", text, None))
        return SimpleNamespace(first=SimpleNamespace(wait_for=self._wait_for_text))

    async def _wait_for_text(self, timeout=10000):
        self.waits.append(("text", "", timeout))

    async def add_script_tag(self, content):
        self.added_scripts.append(content)

    async def add_style_tag(self, content):
        self.added_styles.append(content)

    async def evaluate(self, script, arg=None):
        self.evaluated.append((script, arg))
        if script == "document.documentElement.outerHTML":
            return "<html><body>ready</body></html>"
        if "window.location.href" in script:
            return self.url
        if "localStorage" in script and "sessionStorage" in script and "Object.fromEntries" in script:
            return {"localStorage": dict(self.local_storage), "sessionStorage": dict(self.session_storage)}
        if "window.__ultrastealthRestoreStorage" in script:
            self.local_storage.update(arg.get("localStorage", {}))
            self.session_storage.update(arg.get("sessionStorage", {}))
            return None
        if "localStorage.setItem" in script:
            self.local_storage[arg["key"]] = arg["value"]
            return None
        if "sessionStorage.setItem" in script:
            self.session_storage[arg["key"]] = arg["value"]
            return None
        if "localStorage.getItem" in script:
            return self.local_storage.get(arg)
        if "sessionStorage.getItem" in script:
            return self.session_storage.get(arg)
        if "localStorage.clear" in script:
            self.local_storage.clear()
            return None
        if "sessionStorage.clear" in script:
            self.session_storage.clear()
            return None
        return {"ok": True}


class FakeContext:
    def __init__(self, page):
        self.pages = [page]
        self.added_init_scripts = []
        self.cookies_value = [{"name": "session_id", "value": "abc", "domain": "example.com"}]
        self.added_cookies = []
        self.cleared_cookies = []

    async def new_page(self):
        page = FakePage()
        self.pages.append(page)
        return page

    async def add_init_script(self, script):
        self.added_init_scripts.append(script)

    async def cookies(self):
        return list(self.cookies_value)

    async def add_cookies(self, cookies):
        self.added_cookies.extend(cookies)
        self.cookies_value.extend(cookies)

    async def clear_cookies(self, **kwargs):
        self.cleared_cookies.append(kwargs)
        self.cookies_value = []


class McpBrowserToolTests(unittest.TestCase):
    def setUp(self):
        self.page = FakePage()
        self.context = FakeContext(self.page)
        mcp_server._fetcher = SimpleNamespace(_context=self.context, user_data_dir=None)
        mcp_server._page = self.page
        mcp_server._session_start = 0
        mcp_server._request_count = 0
        mcp_server._active_tab_id = mcp_server._page_id(self.page)
        mcp_server._browser_wedged = False
        mcp_server._browser_config = (None, None, None)
        mcp_server._network_enabled = False
        mcp_server._network_handlers = {}
        mcp_server._diagnostic_handlers = {}
        mcp_server._console_log = []
        mcp_server._page_errors = []

    def test_console_and_errors_are_captured_and_clearable(self):
        mcp_server._attach_page_diagnostics(self.page)

        self.page.emit("console", FakeConsoleMessage(text="cmux-console-entry"))
        self.page.emit("pageerror", RuntimeError("cmux-browser-boom"))

        console_output = asyncio.run(mcp_server.browser_console_list())
        errors_output = asyncio.run(mcp_server.browser_errors_list())

        self.assertIn("cmux-console-entry", console_output)
        self.assertIn("cmux-browser-boom", errors_output)
        self.assertIn("Cleared 1 console", asyncio.run(mcp_server.browser_console_clear()))
        self.assertIn("Cleared 1 error", asyncio.run(mcp_server.browser_errors_clear()))

    def test_wait_supports_url_load_state_and_javascript_function(self):
        self.assertIn(
            "URL contains '/dashboard'",
            asyncio.run(mcp_server.browser_wait(url_contains="/dashboard")),
        )
        self.assertIn(
            "Load state 'networkidle'",
            asyncio.run(mcp_server.browser_wait(load_state="networkidle")),
        )
        self.assertIn(
            "Function returned truthy",
            asyncio.run(mcp_server.browser_wait(javascript="window.__appReady === true")),
        )

        self.assertIn(("url", "**/dashboard**", 10000), self.page.waits)
        self.assertIn(("load_state", "networkidle", 10000), self.page.waits)
        self.assertIn(("function", "window.__appReady === true", 10000), self.page.waits)

    def test_getters_state_checks_focus_and_scroll_helpers(self):
        self.assertIn("ready", asyncio.run(mcp_server.browser_get("text", "#status")))
        self.assertIn("<div", asyncio.run(mcp_server.browser_get("html", "#status")))
        self.assertIn("ops@example.com", asyncio.run(mcp_server.browser_get("value", "#email")))
        self.assertIn("https://example.com/pricing", asyncio.run(mcp_server.browser_get("attr", "#link", attribute="href")))
        self.assertIn("3", asyncio.run(mcp_server.browser_get("count", ".row")))
        self.assertIn('"width": 30', asyncio.run(mcp_server.browser_get("box", "#status")))
        self.assertIn("rgb(255, 0, 0)", asyncio.run(mcp_server.browser_get("styles", "#status")))

        self.assertIn("visible: true", asyncio.run(mcp_server.browser_is("visible", "#status")).lower())
        self.assertIn("enabled: true", asyncio.run(mcp_server.browser_is("enabled", "#submit")).lower())
        self.assertIn("checked: true", asyncio.run(mcp_server.browser_is("checked", "#terms")).lower())
        self.assertIn("Scrolled into view", asyncio.run(mcp_server.browser_scroll_into_view("#status")))
        self.assertIn("Focused", asyncio.run(mcp_server.browser_focus("#email")))

    def test_cookies_storage_and_state_round_trip(self):
        cookie_json = asyncio.run(mcp_server.browser_cookies("get"))
        self.assertEqual(json.loads(cookie_json)[0]["name"], "session_id")

        set_result = asyncio.run(
            mcp_server.browser_cookies(
                "set",
                name="theme",
                value="dark",
                domain="example.com",
                path="/",
            )
        )
        self.assertIn("Set cookie theme", set_result)
        self.assertEqual(self.context.added_cookies[-1]["name"], "theme")

        self.assertIn(
            "Set localStorage theme",
            asyncio.run(mcp_server.browser_storage("local", "set", key="theme", value="dark")),
        )
        self.assertIn("dark", asyncio.run(mcp_server.browser_storage("local", "get", key="theme")))

        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            self.assertIn("Saved browser state", asyncio.run(mcp_server.browser_state_save(str(state_path))))
            payload = json.loads(state_path.read_text())
            self.assertIn("cookies", payload)
            self.assertEqual(payload["origins"][0]["localStorage"]["theme"], "dark")

            self.page.local_storage.clear()
            self.assertIn("Loaded browser state", asyncio.run(mcp_server.browser_state_load(str(state_path))))
            self.assertEqual(self.page.local_storage["theme"], "dark")

        self.assertIn("Cleared cookies", asyncio.run(mcp_server.browser_cookies("clear")))

    def test_screenshot_can_write_to_requested_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "shot.png"
            result = asyncio.run(mcp_server.browser_screenshot(path=str(out)))

            self.assertEqual(out.read_bytes(), b"fake-png")
            self.assertIn(str(out), result[0]["text"])

    async def _sleep(self, delay):
        pass


if __name__ == "__main__":
    unittest.main()
