import asyncio
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import fetcher


MAC_CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


class FakeContext:
    pages = []

    async def add_init_script(self, script):
        self.init_script = script

    async def close(self):
        pass


class FakeChromium:
    def __init__(self, first_launch_error=None):
        self.first_launch_error = first_launch_error
        self.launches = []
        self.user_data_dir = None
        self.launch_kwargs = None

    async def launch_persistent_context(self, user_data_dir, **kwargs):
        self.launches.append((user_data_dir, kwargs))
        self.user_data_dir = user_data_dir
        self.launch_kwargs = kwargs
        if self.first_launch_error and len(self.launches) == 1:
            raise self.first_launch_error
        return FakeContext()


class FakePlaywright:
    def __init__(self, chromium):
        self.chromium = chromium

    async def stop(self):
        pass


class FakePlaywrightManager:
    def __init__(self, chromium):
        self._chromium = chromium

    async def start(self):
        return FakePlaywright(self._chromium)


def fake_rebrowser_modules(chromium):
    rebrowser_module = types.ModuleType("rebrowser_playwright")
    async_api = types.ModuleType("rebrowser_playwright.async_api")
    async_api.async_playwright = lambda: FakePlaywrightManager(chromium)
    return {
        "rebrowser_playwright": rebrowser_module,
        "rebrowser_playwright.async_api": async_api,
    }


class FetcherRunnerTests(unittest.TestCase):
    def setUp(self):
        self._log_info_patcher = patch.object(fetcher.log, "info")
        self._log_info_patcher.start()
        self.addCleanup(self._log_info_patcher.stop)

    def test_find_chrome_detects_macos_google_chrome_app(self):
        def is_mac_chrome(path):
            return path == MAC_CHROME

        with patch.object(fetcher.os.path, "isfile", side_effect=is_mac_chrome), \
             patch.object(fetcher.os, "access", return_value=True), \
             patch.object(fetcher.shutil, "which", return_value=None):
            self.assertEqual(fetcher._find_chrome(), MAC_CHROME)

    def test_macos_default_runner_uses_chrome_default_profile_headful(self):
        chromium = FakeChromium()
        ensure_xvfb = Mock(return_value=None)

        with patch.dict(sys.modules, fake_rebrowser_modules(chromium)), \
             patch.object(sys, "platform", "darwin"), \
             patch.dict(fetcher.os.environ, {}, clear=True), \
             patch.object(fetcher.Path, "home", return_value=Path("/Users/alice")), \
             patch.object(fetcher, "_find_chrome", return_value=MAC_CHROME), \
             patch.object(fetcher, "_ensure_xvfb", ensure_xvfb):
            us = fetcher.UltrastealthFetcher(headless=False)
            asyncio.run(us.start())
            asyncio.run(us.close())

        self.assertEqual(chromium.launch_kwargs["executable_path"], MAC_CHROME)
        self.assertFalse(chromium.launch_kwargs["headless"])
        self.assertEqual(
            chromium.user_data_dir,
            "/Users/alice/Library/Application Support/Google/Chrome",
        )
        self.assertIn("--profile-directory=Default", chromium.launch_kwargs["args"])
        ensure_xvfb.assert_not_called()

    def test_custom_user_data_dir_uses_requested_profile_directory(self):
        chromium = FakeChromium()
        ensure_xvfb = Mock(return_value=None)

        with patch.dict(sys.modules, fake_rebrowser_modules(chromium)), \
             patch.object(sys, "platform", "darwin"), \
             patch.dict(fetcher.os.environ, {}, clear=True), \
             patch.object(fetcher, "_find_chrome", return_value=MAC_CHROME), \
             patch.object(fetcher, "_ensure_xvfb", ensure_xvfb):
            us = fetcher.UltrastealthFetcher(
                headless=False,
                user_data_dir="/tmp/chrome-user-data",
                profile_directory="Profile 3",
            )
            asyncio.run(us.start())
            asyncio.run(us.close())

        self.assertEqual(chromium.user_data_dir, "/tmp/chrome-user-data")
        self.assertIn("--profile-directory=Profile 3", chromium.launch_kwargs["args"])

    def test_default_profile_launch_failure_falls_back_to_temp_profile(self):
        chromium = FakeChromium(
            first_launch_error=RuntimeError(
                "BrowserType.launch_persistent_context: Target page, context or browser has been closed"
            )
        )
        ensure_xvfb = Mock(return_value=None)

        with patch.dict(sys.modules, fake_rebrowser_modules(chromium)), \
             patch.object(sys, "platform", "darwin"), \
             patch.dict(fetcher.os.environ, {}, clear=True), \
             patch.object(fetcher.Path, "home", return_value=Path("/Users/alice")), \
             patch.object(fetcher, "_find_chrome", return_value=MAC_CHROME), \
             patch.object(fetcher.tempfile, "mkdtemp", return_value="/tmp/ultrastealth_retry"), \
             patch.object(fetcher, "_ensure_xvfb", ensure_xvfb), \
             patch.object(fetcher.log, "warning") as warning:
            us = fetcher.UltrastealthFetcher(headless=False)
            asyncio.run(us.start())
            asyncio.run(us.close())

        self.assertEqual(len(chromium.launches), 2)
        first_user_data_dir, first_kwargs = chromium.launches[0]
        second_user_data_dir, second_kwargs = chromium.launches[1]
        self.assertEqual(
            first_user_data_dir,
            "/Users/alice/Library/Application Support/Google/Chrome",
        )
        self.assertEqual(second_user_data_dir, "/tmp/ultrastealth_retry")
        self.assertIn("--profile-directory=Default", first_kwargs["args"])
        self.assertNotIn("--profile-directory=Default", second_kwargs["args"])
        warning.assert_called_once()

    def test_linux_default_profile_launch_failure_falls_back_to_temp_profile(self):
        chromium = FakeChromium(
            first_launch_error=RuntimeError(
                "BrowserType.launch_persistent_context: Target page, context or browser has been closed"
            )
        )
        ensure_xvfb = Mock(return_value=None)

        with patch.dict(sys.modules, fake_rebrowser_modules(chromium)), \
             patch.object(sys, "platform", "linux"), \
             patch.dict(fetcher.os.environ, {}, clear=True), \
             patch.object(fetcher.Path, "home", return_value=Path("/home/alice")), \
             patch.object(fetcher, "_find_chrome", return_value="/usr/bin/google-chrome"), \
             patch.object(fetcher.tempfile, "mkdtemp", return_value="/tmp/ultrastealth_retry"), \
             patch.object(fetcher, "_ensure_xvfb", ensure_xvfb), \
             patch.object(fetcher.log, "warning") as warning:
            us = fetcher.UltrastealthFetcher(headless=False)
            asyncio.run(us.start())
            asyncio.run(us.close())

        self.assertEqual(len(chromium.launches), 2)
        first_user_data_dir, first_kwargs = chromium.launches[0]
        second_user_data_dir, second_kwargs = chromium.launches[1]
        self.assertEqual(first_user_data_dir, "/home/alice/.config/google-chrome")
        self.assertEqual(second_user_data_dir, "/tmp/ultrastealth_retry")
        self.assertIn("--profile-directory=Default", first_kwargs["args"])
        self.assertNotIn("--profile-directory=Default", second_kwargs["args"])
        ensure_xvfb.assert_called_once()
        warning.assert_called_once()

    def test_default_profile_launch_failure_raises_when_temp_fallback_disabled(self):
        chromium = FakeChromium(
            first_launch_error=RuntimeError(
                "BrowserType.launch_persistent_context: Target page, context or browser has been closed"
            )
        )
        ensure_xvfb = Mock(return_value=None)

        with patch.dict(sys.modules, fake_rebrowser_modules(chromium)), \
             patch.object(sys, "platform", "darwin"), \
             patch.dict(fetcher.os.environ, {}, clear=True), \
             patch.object(fetcher.Path, "home", return_value=Path("/Users/alice")), \
             patch.object(fetcher, "_find_chrome", return_value=MAC_CHROME), \
             patch.object(fetcher, "_ensure_xvfb", ensure_xvfb):
            us = fetcher.UltrastealthFetcher(
                headless=False,
                profile_directory="Profile 1",
                fallback_to_temp_profile=False,
            )
            with self.assertRaises(RuntimeError):
                asyncio.run(us.start())

        self.assertEqual(len(chromium.launches), 1)
        self.assertIn("--profile-directory=Profile 1", chromium.launches[0][1]["args"])


if __name__ == "__main__":
    unittest.main()
