import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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
        s = asyncio.run(browser_core.status())
        self.assertTrue(s["warm"])
        self.assertEqual(s["url"], "https://example.com/dashboard")

    def test_snapshot_assigns_stable_refs(self):
        snap = asyncio.run(browser_core.snapshot())
        refs = {e["ref"]: e for e in snap["refs"]}
        self.assertIn("e0", refs)
        self.assertEqual(refs["e0"]["role"], "button")
        self.assertEqual(refs["e0"]["name"], "Submit")
        snap2 = asyncio.run(browser_core.snapshot())
        self.assertEqual([e["ref"] for e in snap["refs"]],
                         [e["ref"] for e in snap2["refs"]])

    def test_snapshot_diff_returns_only_changes(self):
        asyncio.run(browser_core.snapshot())
        diff = asyncio.run(browser_core.snapshot(diff=True))
        self.assertEqual(diff["refs"], [])

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

    def test_focus_and_scroll_into_view(self):
        asyncio.run(browser_core.focus("#email"))
        asyncio.run(browser_core.scroll_into_view("#email"))
        self.assertIn("#email", self.page.focused)
        self.assertIn("#email", self.page.scrolled_into_view)

    def test_scroll_and_go_back_and_reload(self):
        asyncio.run(browser_core.scroll("down", 300))
        res = asyncio.run(browser_core.go_back())
        self.assertEqual(res["url"], "https://example.com/prev")
        self.assertTrue(any("scrollBy" in s for s, _ in self.page.evaluated))
        asyncio.run(browser_core.reload())
        self.assertEqual(self.page.goto_calls[-1][1], "reload")


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
        self.assertEqual(len(res["steps"]), 1)

    def test_batch_unknown_op_errors(self):
        res = asyncio.run(browser_core.batch([{"op": "fly"}]))
        self.assertEqual(res["steps"][0]["error"]["type"], "bad_op")


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

            async def close(self):
                self.closed = True

        with patch.object(browser_core, "UltrastealthFetcher", FakeFetcher):
            asyncio.run(browser_core.ensure_browser())
        self.assertIsNotNone(browser_core._page)
        self.assertEqual(browser_core._page.url, "https://example.com/dashboard")


if __name__ == "__main__":
    unittest.main()
