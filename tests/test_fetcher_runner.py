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
    def __init__(self):
        self.user_data_dir = None
        self.launch_kwargs = None

    async def launch_persistent_context(self, user_data_dir, **kwargs):
        self.user_data_dir = user_data_dir
        self.launch_kwargs = kwargs
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


if __name__ == "__main__":
    unittest.main()
