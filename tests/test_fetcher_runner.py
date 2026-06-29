import asyncio
import contextlib
import io
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import fetcher


MAC_CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
MAC_CHROMIUM = "/Applications/Chromium.app/Contents/MacOS/Chromium"


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

    def test_find_chrome_detects_macos_google_chrome_before_chromium(self):
        def is_mac_chrome(path):
            return path in {MAC_CHROME, MAC_CHROMIUM}

        with patch.object(fetcher.os.path, "isfile", side_effect=is_mac_chrome), \
             patch.object(fetcher.os, "access", return_value=True), \
             patch.object(fetcher.shutil, "which", return_value=None):
            self.assertEqual(fetcher._find_chrome(), MAC_CHROME)

    def test_macos_default_runner_uses_google_chrome_default_profile_headful(self):
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

    def test_explicit_chromium_runner_uses_chromium_default_profile(self):
        chromium = FakeChromium()
        ensure_xvfb = Mock(return_value=None)

        with patch.dict(sys.modules, fake_rebrowser_modules(chromium)), \
             patch.object(sys, "platform", "darwin"), \
             patch.dict(fetcher.os.environ, {}, clear=True), \
             patch.object(fetcher.Path, "home", return_value=Path("/Users/alice")), \
             patch.object(fetcher, "_find_chrome", return_value=MAC_CHROMIUM), \
             patch.object(fetcher, "_ensure_xvfb", ensure_xvfb):
            us = fetcher.UltrastealthFetcher(headless=False, runner="chromium+default-profile")
            asyncio.run(us.start())
            asyncio.run(us.close())

        self.assertEqual(
            chromium.user_data_dir,
            "/Users/alice/Library/Application Support/Chromium",
        )
        self.assertIn("--profile-directory=Default", chromium.launch_kwargs["args"])

    def test_custom_user_data_dir_uses_requested_profile_directory(self):
        chromium = FakeChromium()
        ensure_xvfb = Mock(return_value=None)

        with patch.dict(sys.modules, fake_rebrowser_modules(chromium)), \
             patch.object(sys, "platform", "darwin"), \
             patch.dict(fetcher.os.environ, {}, clear=True), \
            patch.object(fetcher, "_find_chrome", return_value=MAC_CHROMIUM), \
             patch.object(fetcher, "_ensure_xvfb", ensure_xvfb):
            us = fetcher.UltrastealthFetcher(
                headless=False,
                user_data_dir="/tmp/chromium-user-data",
                profile_directory="Profile 3",
            )
            asyncio.run(us.start())
            asyncio.run(us.close())

        self.assertEqual(chromium.user_data_dir, "/tmp/chromium-user-data")
        self.assertIn("--profile-directory=Profile 3", chromium.launch_kwargs["args"])

    def test_macos_launch_window_size_fits_host_screen(self):
        chromium = FakeChromium()
        ensure_xvfb = Mock(return_value=None)

        with patch.dict(sys.modules, fake_rebrowser_modules(chromium)), \
             patch.object(sys, "platform", "darwin"), \
             patch.dict(fetcher.os.environ, {}, clear=True), \
             patch.object(fetcher.Path, "home", return_value=Path("/Users/alice")), \
             patch.object(fetcher, "_find_chrome", return_value=MAC_CHROMIUM), \
             patch.object(fetcher, "_ensure_xvfb", ensure_xvfb), \
             patch.object(fetcher, "_host_screen_size", return_value=(1512, 982), create=True):
            us = fetcher.UltrastealthFetcher(headless=False)
            asyncio.run(us.start())
            asyncio.run(us.close())

        args = chromium.launch_kwargs["args"]
        self.assertIn("--window-size=1432,822", args)
        self.assertNotIn("--start-maximized", args)
        self.assertEqual(chromium.launch_kwargs["viewport"], {"width": 1432, "height": 822})
        self.assertEqual(chromium.launch_kwargs["screen"], {"width": 1512, "height": 982})
        ensure_xvfb.assert_not_called()

    def test_launch_flags_keep_scrollbars_visible_and_include_browser_use_hygiene(self):
        chromium = FakeChromium()
        ensure_xvfb = Mock(return_value=None)

        with patch.dict(sys.modules, fake_rebrowser_modules(chromium)), \
             patch.object(sys, "platform", "darwin"), \
             patch.dict(fetcher.os.environ, {}, clear=True), \
             patch.object(fetcher.Path, "home", return_value=Path("/Users/alice")), \
             patch.object(fetcher, "_find_chrome", return_value=MAC_CHROMIUM), \
             patch.object(fetcher, "_ensure_xvfb", ensure_xvfb), \
             patch.object(fetcher, "_host_screen_size", return_value=(1512, 982), create=True):
            us = fetcher.UltrastealthFetcher(headless=False)
            asyncio.run(us.start())
            asyncio.run(us.close())

        args = chromium.launch_kwargs["args"]
        self.assertNotIn("--hide-scrollbars", args)
        for expected in (
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-search-engine-choice-screen",
            "--disable-infobars",
            "--hide-crash-restore-bubble",
            "--suppress-message-center-popups",
            "--disable-blink-features=AutomationControlled",
        ):
            self.assertIn(expected, args)

    def test_linux_launch_window_size_fits_host_screen(self):
        chromium = FakeChromium()
        ensure_xvfb = Mock(return_value=None)

        with patch.dict(sys.modules, fake_rebrowser_modules(chromium)), \
             patch.object(sys, "platform", "linux"), \
             patch.dict(fetcher.os.environ, {}, clear=True), \
             patch.object(fetcher.Path, "home", return_value=Path("/home/alice")), \
             patch.object(fetcher, "_find_chrome", return_value="/usr/bin/chromium"), \
             patch.object(fetcher, "_ensure_xvfb", ensure_xvfb), \
             patch.object(fetcher, "_host_screen_size", return_value=(1366, 768), create=True):
            us = fetcher.UltrastealthFetcher(headless=False)
            asyncio.run(us.start())
            asyncio.run(us.close())

        args = chromium.launch_kwargs["args"]
        self.assertIn("--window-size=1286,608", args)
        self.assertNotIn("--start-maximized", args)
        self.assertEqual(chromium.launch_kwargs["viewport"], {"width": 1286, "height": 608})
        self.assertEqual(chromium.launch_kwargs["screen"], {"width": 1366, "height": 768})
        ensure_xvfb.assert_called_once()

    def test_start_applies_rebrowser_patch_before_launch(self):
        events = []

        class RecordingChromium(FakeChromium):
            async def launch_persistent_context(self, user_data_dir, **kwargs):
                events.append("launch")
                return await super().launch_persistent_context(user_data_dir, **kwargs)

        chromium = RecordingChromium()
        ensure_xvfb = Mock(return_value=None)
        patcher = Mock()
        patcher.is_patched.side_effect = [False, True]

        def apply_patch(mode):
            events.append("patch")
            return 0

        patcher.run.side_effect = apply_patch

        with patch.dict(sys.modules, fake_rebrowser_modules(chromium)), \
             patch.object(sys, "platform", "darwin"), \
             patch.dict(fetcher.os.environ, {}, clear=True), \
             patch.object(fetcher.Path, "home", return_value=Path("/Users/alice")), \
             patch.object(fetcher, "_find_chrome", return_value=MAC_CHROMIUM), \
             patch.object(fetcher, "_ensure_xvfb", ensure_xvfb), \
             patch.object(fetcher, "_load_patch_rebrowser", return_value=patcher, create=True):
            us = fetcher.UltrastealthFetcher(headless=False)
            asyncio.run(us.start())
            asyncio.run(us.close())

        patcher.run.assert_called_once_with("apply")
        self.assertEqual(events, ["patch", "launch"])

    def test_runtime_rebrowser_patch_application_is_quiet(self):
        patcher = Mock()
        patcher.is_patched.side_effect = [False, True]

        def noisy_patch(mode):
            print(f"PATCH {mode}")
            return 0

        patcher.run.side_effect = noisy_patch
        stdout = io.StringIO()

        with patch.object(fetcher, "_load_patch_rebrowser", return_value=patcher, create=True), \
             contextlib.redirect_stdout(stdout):
            self.assertTrue(fetcher._ensure_rebrowser_patched())

        patcher.run.assert_called_once_with("apply")
        self.assertEqual(stdout.getvalue(), "")

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
            patch.object(fetcher, "_find_chrome", return_value=MAC_CHROMIUM), \
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
            patch.object(fetcher, "_find_chrome", return_value=MAC_CHROMIUM), \
             patch.object(fetcher, "_ensure_xvfb", ensure_xvfb):
            us = fetcher.UltrastealthFetcher(
                headless=False,
                profile_directory="Profile 1",
                fallback_to_temp_profile=False,
            )
            with self.assertRaisesRegex(RuntimeError, "Could not launch requested browser profile"):
                asyncio.run(us.start())

        self.assertEqual(len(chromium.launches), 1)
        self.assertIn("--profile-directory=Profile 1", chromium.launches[0][1]["args"])

    def test_env_profile_launch_failure_raises_without_temp_fallback(self):
        chromium = FakeChromium(
            first_launch_error=RuntimeError(
                "BrowserType.launch_persistent_context: Target page, context or browser has been closed"
            )
        )
        ensure_xvfb = Mock(return_value=None)

        with patch.dict(sys.modules, fake_rebrowser_modules(chromium)), \
            patch.object(sys, "platform", "darwin"), \
            patch.dict(
                fetcher.os.environ,
                {
                    "ULTRASTEALTH_USER_DATA_DIR": "/Users/alice/Library/Application Support/Chromium",
                    "ULTRASTEALTH_PROFILE_DIRECTORY": "Profile 6",
                },
                clear=True,
            ), \
            patch.object(fetcher.Path, "home", return_value=Path("/Users/alice")), \
            patch.object(fetcher, "_find_chrome", return_value=MAC_CHROMIUM), \
            patch.object(fetcher, "_ensure_xvfb", ensure_xvfb):
            us = fetcher.UltrastealthFetcher(headless=False)
            with self.assertRaisesRegex(RuntimeError, "Could not launch requested browser profile"):
                asyncio.run(us.start())
            asyncio.run(us.close())

        self.assertEqual(len(chromium.launches), 1)
        user_data_dir, launch_kwargs = chromium.launches[0]
        self.assertEqual(user_data_dir, "/Users/alice/Library/Application Support/Chromium")
        self.assertIn("--profile-directory=Profile 6", launch_kwargs["args"])

    def test_macos_chrome_env_uses_requested_chrome_profile_without_fallback(self):
        chromium = FakeChromium(
            first_launch_error=RuntimeError(
                "BrowserType.launch_persistent_context: Target page, context or browser has been closed"
            )
        )
        ensure_xvfb = Mock(return_value=None)

        with patch.dict(sys.modules, fake_rebrowser_modules(chromium)), \
            patch.object(sys, "platform", "darwin"), \
            patch.dict(
                fetcher.os.environ,
                {
                    "ULTRASTEALTH_RUNNER": "chrome+default-profile",
                    "ULTRASTEALTH_USER_DATA_DIR": "/Users/alice/Library/Application Support/Google/Chrome",
                    "ULTRASTEALTH_PROFILE_DIRECTORY": "Profile 6",
                },
                clear=True,
            ), \
            patch.object(fetcher.Path, "home", return_value=Path("/Users/alice")), \
            patch.object(fetcher, "_find_chrome", return_value=MAC_CHROME), \
            patch.object(fetcher, "_ensure_xvfb", ensure_xvfb), \
            patch.object(fetcher.log, "warning"):
            us = fetcher.UltrastealthFetcher(headless=False)
            with self.assertRaisesRegex(RuntimeError, "Could not launch requested browser profile"):
                asyncio.run(us.start())
            asyncio.run(us.close())

        self.assertEqual(len(chromium.launches), 1)
        first_user_data_dir, first_kwargs = chromium.launches[0]
        self.assertEqual(first_user_data_dir, "/Users/alice/Library/Application Support/Google/Chrome")
        self.assertIn("--profile-directory=Profile 6", first_kwargs["args"])

    def test_linux_chrome_env_uses_google_chrome_profile(self):
        chromium = FakeChromium()
        ensure_xvfb = Mock(return_value=None)

        with patch.dict(sys.modules, fake_rebrowser_modules(chromium)), \
            patch.object(sys, "platform", "linux"), \
            patch.dict(
                fetcher.os.environ,
                {
                    "ULTRASTEALTH_USER_DATA_DIR": "/home/alice/.config/google-chrome",
                    "ULTRASTEALTH_PROFILE_DIRECTORY": "Profile 6",
                },
                clear=True,
            ), \
            patch.object(fetcher.Path, "home", return_value=Path("/home/alice")), \
            patch.object(fetcher, "_find_chrome", return_value="/usr/bin/google-chrome"), \
            patch.object(fetcher, "_ensure_xvfb", ensure_xvfb):
            us = fetcher.UltrastealthFetcher(headless=False)
            asyncio.run(us.start())
            asyncio.run(us.close())

        self.assertEqual(chromium.user_data_dir, "/home/alice/.config/google-chrome")
        self.assertIn("--profile-directory=Profile 6", chromium.launch_kwargs["args"])

    def test_stale_chrome_singletons_are_removed_before_launch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "SingletonLock").symlink_to("MacBook-Pro.local-999999")
            (root / "SingletonSocket").symlink_to("/tmp/missing-chrome-singleton-socket")
            (root / "SingletonCookie").symlink_to("123456789")

            removed = fetcher._cleanup_stale_chrome_singletons(str(root))

            self.assertTrue(removed)
            self.assertFalse(os.path.lexists(root / "SingletonLock"))
            self.assertFalse(os.path.lexists(root / "SingletonSocket"))
            self.assertFalse(os.path.lexists(root / "SingletonCookie"))

    def test_live_chrome_singletons_are_preserved_before_launch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "SingletonLock").symlink_to(f"MacBook-Pro.local-{os.getpid()}")
            (root / "SingletonSocket").symlink_to("/tmp/live-chrome-singleton-socket")
            (root / "SingletonCookie").symlink_to("123456789")

            removed = fetcher._cleanup_stale_chrome_singletons(str(root))

            self.assertFalse(removed)
            self.assertTrue(os.path.lexists(root / "SingletonLock"))
            self.assertTrue(os.path.lexists(root / "SingletonSocket"))
            self.assertTrue(os.path.lexists(root / "SingletonCookie"))

    def test_start_removes_stale_chrome_singletons_before_persistent_launch(self):
        chromium = FakeChromium()
        ensure_xvfb = Mock(return_value=None)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "SingletonLock").symlink_to("MacBook-Pro.local-999999")
            (root / "SingletonSocket").symlink_to("/tmp/missing-chrome-singleton-socket")
            (root / "SingletonCookie").symlink_to("123456789")

            with patch.dict(sys.modules, fake_rebrowser_modules(chromium)), \
                patch.object(sys, "platform", "darwin"), \
                patch.dict(fetcher.os.environ, {}, clear=True), \
                patch.object(fetcher, "_find_chrome", return_value=MAC_CHROMIUM), \
                patch.object(fetcher, "_ensure_xvfb", ensure_xvfb):
                us = fetcher.UltrastealthFetcher(
                    headless=False,
                    user_data_dir=str(root),
                    profile_directory="Profile 6",
                )
                asyncio.run(us.start())
                asyncio.run(us.close())

            self.assertFalse(os.path.lexists(root / "SingletonLock"))
            self.assertFalse(os.path.lexists(root / "SingletonSocket"))
            self.assertFalse(os.path.lexists(root / "SingletonCookie"))
            self.assertEqual(chromium.user_data_dir, str(root))


if __name__ == "__main__":
    unittest.main()
