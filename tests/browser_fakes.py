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

    async def focus(self, timeout=5000):
        self.page.focused.append(self.key)

    async def scroll_into_view_if_needed(self, timeout=5000):
        self.page.scrolled_into_view.append(self.key)

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


class _Keyboard:
    def __init__(self, page):
        self.page = page

    async def press(self, key):
        self.page.pressed.append(("keyboard", key))


class FakePage:
    def __init__(self, tree=None):
        self.url = "https://example.com/dashboard"
        self.accessibility = FakeAccessibility(tree if tree is not None else _DEFAULT_TREE)
        self.clicked, self.filled, self.typed = [], [], []
        self.pressed, self.hovered, self.selected = [], [], []
        self.focused, self.scrolled_into_view = [], []
        self.evaluated, self.waits, self.goto_calls = [], [], []
        self.text_by_selector = {"#status": "ready"}
        self.html_by_selector = {"#status": "<div>ready</div>"}
        self.attr_by_selector = {"#link": {"href": "https://example.com/p"}}
        self.visible_by_selector = {"#status": True}
        self.enabled_by_selector = {"#submit": True}
        self.checked_by_selector = {"#terms": True}
        self.keyboard = _Keyboard(self)
        self.context = None  # set by make_fetcher()/FakeContext.new_page()
        self._closed = False

    def is_closed(self):
        return self._closed

    async def close(self):
        self._closed = True

    async def title(self):
        return "Dashboard"

    async def goto(self, url, wait_until="load", timeout=30000):
        self.goto_calls.append((url, wait_until))
        self.url = url

    async def reload(self, wait_until="load", timeout=30000):
        self.goto_calls.append((self.url, "reload"))

    async def go_back(self, wait_until="load", timeout=30000):
        self.url = "https://example.com/prev"

    def locator(self, selector):
        return FakeLocator(self, selector)

    def get_by_role(self, role, name=""):
        return FakeLocator(self, f"role:{role}:{name}")

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
        self.cookies_value = [
            {"name": "sid", "value": "abc123", "domain": "example.com", "path": "/"},
        ]

    async def new_page(self):
        p = FakePage()
        p.context = self
        self.pages.append(p)
        return p

    async def cookies(self):
        return self.cookies_value


_DEFAULT_TREE = {
    "role": "WebArea", "name": "Dashboard", "children": [
        {"role": "button", "name": "Submit"},
        {"role": "textbox", "name": "Email"},
        {"role": "link", "name": "Pricing"},
    ],
}


def make_fetcher(page):
    """A minimal fake fetcher whose _context yields fresh pages."""
    ctx = FakeContext(page)
    page.context = ctx
    return SimpleNamespace(_context=ctx, user_data_dir=None)
