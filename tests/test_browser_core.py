import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
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

    def test_cookies_returns_context_cookies(self):
        res = asyncio.run(browser_core.cookies())
        self.assertEqual(res, {"cookies": [
            {"name": "sid", "value": "abc123", "domain": "example.com", "path": "/"},
        ]})

    def test_cookies_registered_in_ops(self):
        self.assertIs(browser_core.OPS["cookies"], browser_core.cookies)

    def test_cookies_wraps_context_failure_in_browser_core_error(self):
        async def boom():
            raise RuntimeError("context gone")
        self.page.context.cookies = boom
        with self.assertRaises(browser_core.BrowserCoreError) as ctx:
            asyncio.run(browser_core.cookies())
        self.assertEqual(ctx.exception.type, "cookies_failed")


class CoreOpLockTests(unittest.TestCase):
    """get_op_lock is behaviorally inert today (every call site resolves to
    the same "default" key), but its stated purpose -- letting a future
    multi-session daemon hand each session its own lock -- was previously
    entirely unverified. These tests check the actual session-keying
    behavior, not just that a lock object comes back.
    """

    def setUp(self):
        browser_core.reset_state_for_tests(fetcher=None, page=None)

    def test_same_session_returns_the_same_lock_instance(self):
        lock1 = browser_core.get_op_lock("alice")
        lock2 = browser_core.get_op_lock("alice")
        self.assertIs(lock1, lock2)

    def test_distinct_sessions_get_distinct_locks(self):
        lock_a = browser_core.get_op_lock("alice")
        lock_b = browser_core.get_op_lock("bob")
        self.assertIsNot(lock_a, lock_b)

    def test_none_defaults_to_the_shared_default_session(self):
        lock_default = browser_core.get_op_lock(None)
        lock_explicit = browser_core.get_op_lock("default")
        self.assertIs(lock_default, lock_explicit)

    def test_locking_one_session_does_not_block_another(self):
        async def scenario():
            lock_a = browser_core.get_op_lock("alice")
            lock_b = browser_core.get_op_lock("bob")
            await lock_a.acquire()
            try:
                # Must not deadlock/block: session "bob" has its own lock.
                acquired = await asyncio.wait_for(lock_b.acquire(), timeout=1)
                self.assertTrue(acquired)
                lock_b.release()
            finally:
                lock_a.release()

        asyncio.run(scenario())

    def test_reset_state_for_tests_clears_locks(self):
        lock1 = browser_core.get_op_lock("alice")
        browser_core.reset_state_for_tests(fetcher=None, page=None)
        lock2 = browser_core.get_op_lock("alice")
        self.assertIsNot(lock1, lock2, "reset must not leak locks across tests")


class CoreHealthTests(unittest.TestCase):
    def setUp(self):
        self.page = FakePage()
        browser_core.reset_state_for_tests(fetcher=make_fetcher(self.page), page=self.page)

    def test_healthy_when_process_alive_and_cdp_responds(self):
        with patch.object(browser_core, "_process_alive", return_value=True):
            diag = asyncio.run(browser_core.health_check())
        self.assertEqual(diag, {"state": "healthy", "process_alive": True, "cdp_ok": True})

    def test_process_exited_skips_the_cdp_probe_entirely(self):
        async def boom():
            raise AssertionError("CDP probe must be skipped once the process is confirmed gone")
        self.page.title = boom
        with patch.object(browser_core, "_process_alive", return_value=False):
            diag = asyncio.run(browser_core.health_check())
        self.assertEqual(diag, {"state": "process_exited", "process_alive": False, "cdp_ok": False})

    def test_unresponsive_when_process_alive_but_cdp_times_out(self):
        async def hang():
            await asyncio.sleep(100)
        self.page.title = hang
        with patch.object(browser_core, "_process_alive", return_value=True):
            diag = asyncio.run(browser_core.health_check(cdp_timeout=0.05))
        self.assertEqual(diag, {"state": "unresponsive", "process_alive": True, "cdp_ok": False})

    def test_no_browser_raises_same_as_other_inspectors(self):
        browser_core.reset_state_for_tests(fetcher=None, page=None)
        with self.assertRaises(browser_core.BrowserCoreError) as ctx:
            asyncio.run(browser_core.health_check())
        self.assertEqual(ctx.exception.type, "no_browser")


class ProcessAliveTests(unittest.TestCase):
    def test_none_without_fetcher(self):
        self.assertIsNone(browser_core._process_alive(None))

    def test_none_without_user_data_dir(self):
        self.assertIsNone(browser_core._process_alive(SimpleNamespace(user_data_dir=None)))

    def test_true_when_matching_chrome_process_found(self):
        fetcher = SimpleNamespace(user_data_dir="/tmp/profile-x")
        proc = SimpleNamespace(
            info={"name": "Google Chrome", "cmdline": ["chrome", "--user-data-dir=/tmp/profile-x"]})
        with patch.object(browser_core.psutil, "process_iter", return_value=[proc]):
            self.assertTrue(browser_core._process_alive(fetcher))

    def test_false_when_no_matching_process_found(self):
        fetcher = SimpleNamespace(user_data_dir="/tmp/profile-x")
        proc = SimpleNamespace(info={"name": "Finder", "cmdline": []})
        with patch.object(browser_core.psutil, "process_iter", return_value=[proc]):
            self.assertFalse(browser_core._process_alive(fetcher))


class CoreFindTests(unittest.TestCase):
    def setUp(self):
        self.page = FakePage()
        browser_core.reset_state_for_tests(fetcher=make_fetcher(self.page), page=self.page)

    def test_find_exact_name_match(self):
        res = asyncio.run(browser_core.find("Submit"))
        self.assertEqual(res["ref"], "e0")
        self.assertEqual(res["role"], "button")
        self.assertEqual(res["name"], "Submit")
        self.assertIn("Submit", res["label"])
        self.assertGreater(res["score"], 0)

    def test_find_fuzzy_partial_match(self):
        res = asyncio.run(browser_core.find("email field"))
        self.assertEqual(res["ref"], "e1")
        self.assertEqual(res["role"], "textbox")

    def test_find_matches_by_role_or_token_overlap(self):
        res = asyncio.run(browser_core.find("pricing link"))
        self.assertEqual(res["ref"], "e2")
        self.assertEqual(res["name"], "Pricing")

    def test_find_registered_in_ops(self):
        self.assertIs(browser_core.OPS["find"], browser_core.find)

    def test_find_raises_when_no_interactive_elements(self):
        empty_page = FakePage(tree={"role": "WebArea", "name": "Empty", "children": []})
        browser_core.reset_state_for_tests(fetcher=make_fetcher(empty_page), page=empty_page)
        with self.assertRaises(browser_core.BrowserCoreError) as ctx:
            asyncio.run(browser_core.find("anything"))
        self.assertEqual(ctx.exception.type, "no_matches")

    def test_match_score_prefers_exact_over_unrelated(self):
        entry_exact = {"role": "button", "name": "Submit"}
        entry_unrelated = {"role": "link", "name": "Terms of Service"}
        self.assertGreater(
            browser_core._match_score("submit", entry_exact),
            browser_core._match_score("submit", entry_unrelated),
        )


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

    def test_ensure_browser_reuses_last_profile_after_close(self):
        page = FakePage()

        class FakeFetcher:
            instances = []

            def __init__(self, **kw):
                self.kwargs = kw
                self._context = make_fetcher(page)._context
                self.user_data_dir = kw.get("user_data_dir")
                self.closed = False
                FakeFetcher.instances.append(self)

            async def start(self):
                self.started = True

            async def close(self):
                self.closed = True

        with patch.object(browser_core, "UltrastealthFetcher", FakeFetcher), \
            patch.dict(
                browser_core.os.environ,
                {
                    "ULTRASTEALTH_RUNNER": "chrome+default-profile",
                    "ULTRASTEALTH_USER_DATA_DIR": "/Users/alice/Library/Application Support/Google/Chrome",
                    "ULTRASTEALTH_PROFILE_DIRECTORY": "Profile 6",
                },
                clear=True,
            ):
            asyncio.run(
                browser_core.ensure_browser(
                    runner="chrome+default-profile",
                    user_data_dir="/tmp/ultrastealth-isolated-profile",
                    profile_directory="Default",
                )
            )
            asyncio.run(browser_core.close())
            asyncio.run(browser_core.ensure_browser())

        self.assertEqual(len(FakeFetcher.instances), 2)
        self.assertTrue(FakeFetcher.instances[0].closed)
        self.assertEqual(
            FakeFetcher.instances[1].kwargs["user_data_dir"],
            "/tmp/ultrastealth-isolated-profile",
        )
        self.assertEqual(FakeFetcher.instances[1].kwargs["profile_directory"], "Default")


if __name__ == "__main__":
    unittest.main()
