import unittest
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
