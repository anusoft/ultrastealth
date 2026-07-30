import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import patch

import bot_benchmark


async def fake_extraction(*args, **kwargs):
    return {
        "tests": [
            {"name": "page_loaded", "passed": True, "value": "ok"},
            {"name": "automation_detected", "passed": False, "value": "detected"},
        ],
        "meta": {"title": "demo"},
    }


class BotBenchmarkSiteTests(unittest.IsolatedAsyncioTestCase):
    def test_user_requested_sites_are_registered(self):
        for site in (
            "brotector",
            "seleniumdetector",
            "recaptcha_v2_invisible",
            "recaptcha_v3",
            "turnstiledemo",
            "egp_announcements",
        ):
            self.assertIn(site, bot_benchmark.SITES)

    def test_lightpanda_method_is_registered(self):
        self.assertIn("lightpanda", bot_benchmark.METHODS)

    def test_lightpanda_serve_command_sets_chrome_like_user_agent(self):
        with patch.dict("os.environ", {}, clear=True):
            command = bot_benchmark._lightpanda_serve_command("/bin/lightpanda", 9333)

        self.assertIn("--user-agent", command)
        user_agent = command[command.index("--user-agent") + 1]
        self.assertIn("Chrome/", user_agent)
        self.assertNotIn("Lightpanda", user_agent)
        self.assertNotIn("Mozilla", user_agent)

    async def test_run_extraction_dispatches_lightpanda_method(self):
        class FakeUltrastealthFetcher:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            async def fetch_and_evaluate(self, *args, **kwargs):
                return {"engine": "ultrastealth"}

        async def fake_lightpanda_extraction(*args, **kwargs):
            return {"engine": "lightpanda", "args": args, "kwargs": kwargs}

        with patch.dict(
            "sys.modules",
            {"ultrastealth": SimpleNamespace(UltrastealthFetcher=FakeUltrastealthFetcher)},
        ), patch.object(
            bot_benchmark,
            "_run_lightpanda_extraction",
            side_effect=fake_lightpanda_extraction,
            create=True,
        ):
            result = await bot_benchmark._run_extraction(
                "lightpanda",
                "https://example.test/",
                "() => ({})",
                wait_secs=1.25,
                pre_eval_js=["window.ready = true"],
                solve_cloudflare=True,
            )

        self.assertEqual(result["engine"], "lightpanda")
        self.assertEqual(result["args"][0], "https://example.test/")
        self.assertEqual(result["args"][1], "() => ({})")
        self.assertEqual(result["kwargs"]["wait_secs"], 1.25)
        self.assertEqual(result["kwargs"]["pre_eval_js"], ["window.ready = true"])
        self.assertTrue(result["kwargs"]["solve_cloudflare"])

    def test_obscura_method_is_registered(self):
        self.assertIn("obscura", bot_benchmark.METHODS)

    def test_obscura_serve_command_enables_stealth_and_sets_user_agent(self):
        with patch.dict("os.environ", {}, clear=True):
            command = bot_benchmark._obscura_serve_command("/bin/obscura", 9333)

        self.assertIn("--stealth", command)
        self.assertIn("--user-agent", command)
        user_agent = command[command.index("--user-agent") + 1]
        self.assertIn("Chrome/", user_agent)
        self.assertIn("Mozilla/5.0", user_agent)

    def test_obscura_serve_command_respects_stealth_opt_out(self):
        with patch.dict("os.environ", {"OBSCURA_STEALTH": "0"}, clear=True):
            command = bot_benchmark._obscura_serve_command("/bin/obscura", 9333)

        self.assertNotIn("--stealth", command)

    async def test_run_extraction_dispatches_obscura_method(self):
        class FakeUltrastealthFetcher:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return None

            async def fetch_and_evaluate(self, *args, **kwargs):
                return {"engine": "ultrastealth"}

        async def fake_obscura_extraction(*args, **kwargs):
            return {"engine": "obscura", "args": args, "kwargs": kwargs}

        with patch.dict(
            "sys.modules",
            {"ultrastealth": SimpleNamespace(UltrastealthFetcher=FakeUltrastealthFetcher)},
        ), patch.object(
            bot_benchmark,
            "_run_obscura_extraction",
            side_effect=fake_obscura_extraction,
            create=True,
        ):
            result = await bot_benchmark._run_extraction(
                "obscura",
                "https://example.test/",
                "() => ({})",
                wait_secs=1.25,
                pre_eval_js=["window.ready = true"],
                solve_cloudflare=True,
            )

        self.assertEqual(result["engine"], "obscura")
        self.assertEqual(result["args"][0], "https://example.test/")
        self.assertEqual(result["args"][1], "() => ({})")
        self.assertEqual(result["kwargs"]["wait_secs"], 1.25)
        self.assertEqual(result["kwargs"]["pre_eval_js"], ["window.ready = true"])
        self.assertTrue(result["kwargs"]["solve_cloudflare"])

    async def test_obscura_extraction_uses_plain_context_for_cdp_compatibility(self):
        class FakePage:
            async def goto(self, *args, **kwargs):
                return None

            async def evaluate(self, *args, **kwargs):
                return {"ok": True}

            async def close(self):
                return None

        class FakeContext:
            async def new_page(self):
                return FakePage()

            async def close(self):
                return None

        class FakeBrowser:
            contexts = []

            async def new_context(self, **kwargs):
                return FakeContext()

            async def close(self):
                return None

        class FakeChromium:
            async def connect_over_cdp(self, *args, **kwargs):
                return FakeBrowser()

        class FakePlaywright:
            chromium = FakeChromium()

            async def stop(self):
                return None

        class FakePlaywrightManager:
            async def start(self):
                return FakePlaywright()

        @asynccontextmanager
        async def fake_obscura_endpoint():
            yield "http://127.0.0.1:9222"

        with patch.dict(
            "sys.modules",
            {
                "rebrowser_playwright.async_api": SimpleNamespace(
                    async_playwright=lambda: FakePlaywrightManager()
                ),
            },
        ), patch.object(bot_benchmark, "_obscura_endpoint", fake_obscura_endpoint):
            result = await bot_benchmark._run_obscura_extraction(
                "https://example.test/",
                "() => ({ ok: true })",
                wait_secs=0,
            )

        self.assertEqual(result, {"ok": True})

    async def test_lightpanda_extraction_uses_plain_context_for_cdp_compatibility(self):
        context_kwargs = []
        init_scripts = []

        class FakePage:
            async def goto(self, *args, **kwargs):
                return None

            async def evaluate(self, *args, **kwargs):
                return {"ok": True}

            async def close(self):
                return None

        class FakeContext:
            async def add_init_script(self, script):
                init_scripts.append(script)

            async def new_page(self):
                return FakePage()

            async def close(self):
                return None

        class FakeBrowser:
            contexts = []

            async def new_context(self, **kwargs):
                context_kwargs.append(kwargs)
                return FakeContext()

            async def close(self):
                return None

        class FakeChromium:
            async def connect_over_cdp(self, *args, **kwargs):
                return FakeBrowser()

        class FakePlaywright:
            chromium = FakeChromium()

            async def stop(self):
                return None

        class FakePlaywrightManager:
            async def start(self):
                return FakePlaywright()

        @asynccontextmanager
        async def fake_lightpanda_endpoint():
            yield "http://127.0.0.1:9222"

        with patch.dict(
            "sys.modules",
            {
                "rebrowser_playwright.async_api": SimpleNamespace(
                    async_playwright=lambda: FakePlaywrightManager()
                ),
            },
        ), patch.object(bot_benchmark, "_lightpanda_endpoint", fake_lightpanda_endpoint):
            result = await bot_benchmark._run_lightpanda_extraction(
                "https://example.test/",
                "() => ({ ok: true })",
                wait_secs=0,
            )

        self.assertEqual(result, {"ok": True})
        self.assertEqual(context_kwargs, [{}])
        self.assertEqual(len(init_scripts), 1)
        self.assertIn("Navigator.prototype", init_scripts[0])
        self.assertIn("webdriver", init_scripts[0])

    async def test_seleniumdetector_scores_extracted_tests(self):
        with patch.object(bot_benchmark, "_run_extraction", side_effect=fake_extraction):
            result = await bot_benchmark.test_seleniumdetector("ultrastealth")

        self.assertEqual(result.site, "seleniumdetector")
        self.assertEqual(result.passed, 1)
        self.assertEqual(result.failed, 1)
        self.assertEqual(result.total, 2)
        self.assertEqual(result.raw, {"title": "demo"})

    async def test_seleniumdetector_prefers_page_passed_verdict_over_input_label(self):
        async def misleading_extraction(*args, **kwargs):
            return {
                "tests": [
                    {"name": "navigator.webdriver", "passed": True, "value": "False"},
                    {"name": "selenium_globals_absent", "passed": True, "value": ""},
                    {"name": "page_verdict", "passed": False, "value": "window.token"},
                ],
                "meta": {
                    "body_snippet": "Chromedriver Detector Passed!\nFor the interactions test fill the input fields...",
                },
            }

        with patch.object(bot_benchmark, "_run_extraction", side_effect=misleading_extraction):
            result = await bot_benchmark.test_seleniumdetector("ultrastealth")

        verdict = next(t for t in result.tests if t["name"] == "page_verdict")
        self.assertTrue(verdict["passed"])
        self.assertEqual(verdict["severity"], "pass")
        self.assertEqual(result.failed, 0)

    async def test_creepjs_scores_low_like_headless_percentage_as_pass(self):
        async def creepjs_extraction(*args, **kwargs):
            return {
                "meta": {
                    "has_headless_warning": True,
                    "like_headless_pct": "31%",
                    "has_bot_warning": False,
                    "has_lie_warning": False,
                },
            }

        with patch.object(bot_benchmark, "_run_extraction", side_effect=creepjs_extraction):
            result = await bot_benchmark.test_creepjs("ultrastealth")

        verdict = next(t for t in result.tests if t["name"] == "headless_detection")
        self.assertTrue(verdict["passed"])
        self.assertEqual(verdict["value"], "31% like headless")

    async def test_challenge_demo_probes_request_solver_when_needed(self):
        calls = []

        async def capture_call(*args, **kwargs):
            calls.append((args, kwargs))
            return await fake_extraction(*args, **kwargs)

        with patch.object(bot_benchmark, "_run_extraction", side_effect=capture_call):
            result = await bot_benchmark.test_turnstiledemo("ultrastealth")

        self.assertEqual(result.site, "turnstiledemo")
        self.assertTrue(calls[0][1]["solve_cloudflare"])


if __name__ == "__main__":
    unittest.main()
