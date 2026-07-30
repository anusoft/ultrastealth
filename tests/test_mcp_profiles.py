import asyncio
import unittest
from unittest.mock import patch

import mcp_server


class FakePage:
    def __init__(self):
        self.url = "about:blank"

    async def goto(self, url, **kwargs):
        self.url = url

    async def title(self):
        return "Example"

    def is_closed(self):
        return False


class FakeContext:
    def __init__(self):
        self.pages = []

    async def new_page(self):
        page = FakePage()
        self.pages.append(page)
        return page


class FakeFetcher:
    instances = []

    def __init__(
        self,
        headless=False,
        runner=None,
        user_data_dir=None,
        profile_directory=None,
        fallback_to_temp_profile=True,
    ):
        self.headless = headless
        self.runner = runner
        self.user_data_dir = user_data_dir
        self.profile_directory = profile_directory
        self.fallback_to_temp_profile = fallback_to_temp_profile
        self._context = FakeContext()
        self.closed = False
        FakeFetcher.instances.append(self)

    async def start(self):
        pass

    async def close(self):
        self.closed = True


class McpProfileTests(unittest.TestCase):
    def setUp(self):
        FakeFetcher.instances = []
        mcp_server._fetcher = None
        mcp_server._page = None
        mcp_server._session_start = None
        mcp_server._request_count = 0
        mcp_server._active_tab_id = None
        mcp_server._browser_wedged = False
        mcp_server._browser_config = (None, None, None)
        mcp_server._network_enabled = False
        mcp_server._network_handlers = {}

    def test_navigate_accepts_profile_directory_for_first_launch(self):
        with patch.object(mcp_server, "UltrastealthFetcher", FakeFetcher), \
             patch.object(mcp_server.asyncio, "sleep", new=self._sleep), \
             patch.object(mcp_server.log, "info"):
            result = asyncio.run(
                mcp_server.browser_navigate(
                    "https://example.com",
                    profile_directory="Profile 2",
                )
            )

        self.assertIn("Navigated to: https://example.com", result)
        self.assertEqual(len(FakeFetcher.instances), 1)
        self.assertEqual(FakeFetcher.instances[0].profile_directory, "Profile 2")
        self.assertFalse(FakeFetcher.instances[0].fallback_to_temp_profile)

    def test_navigate_restarts_when_requested_profile_changes(self):
        with patch.object(mcp_server, "UltrastealthFetcher", FakeFetcher), \
             patch.object(mcp_server.asyncio, "sleep", new=self._sleep), \
             patch.object(mcp_server.log, "info"):
            asyncio.run(
                mcp_server.browser_navigate(
                    "https://example.com/one",
                    profile_directory="Profile 1",
                )
            )
            asyncio.run(
                mcp_server.browser_navigate(
                    "https://example.com/two",
                    profile_directory="Profile 2",
                )
            )

        self.assertEqual(len(FakeFetcher.instances), 2)
        self.assertTrue(FakeFetcher.instances[0].closed)
        self.assertEqual(FakeFetcher.instances[1].profile_directory, "Profile 2")
        self.assertFalse(FakeFetcher.instances[1].fallback_to_temp_profile)

    def test_restart_accepts_profile_directory(self):
        with patch.object(mcp_server, "UltrastealthFetcher", FakeFetcher), \
            patch.object(mcp_server.asyncio, "sleep", new=self._sleep), \
            patch.object(mcp_server.log, "info"):
            result = asyncio.run(
                mcp_server.browser_restart(
                    navigate_to="https://example.com",
                    profile_directory="Profile 3",
                )
            )

        self.assertIn("Browser restarted. Navigated to: https://example.com", result)
        self.assertEqual(len(FakeFetcher.instances), 1)
        self.assertEqual(FakeFetcher.instances[0].profile_directory, "Profile 3")
        self.assertFalse(FakeFetcher.instances[0].fallback_to_temp_profile)

    def test_tool_runner_chrome_runner_is_preserved(self):
        with patch.object(mcp_server, "UltrastealthFetcher", FakeFetcher), \
            patch.object(mcp_server.asyncio, "sleep", new=self._sleep), \
            patch.object(mcp_server.log, "info"):
            result = asyncio.run(
                mcp_server.browser_navigate(
                    "https://example.com",
                    runner="chrome+default-profile",
                )
            )

        self.assertIn("Navigated to: https://example.com", result)
        self.assertEqual(FakeFetcher.instances[0].runner, "chrome+default-profile")

    def test_env_profile_config_disables_temp_fallback_for_first_launch(self):
        with patch.object(mcp_server, "UltrastealthFetcher", FakeFetcher), \
            patch.object(mcp_server.asyncio, "sleep", new=self._sleep), \
            patch.object(mcp_server.log, "info"), \
            patch.object(mcp_server.Path, "home", return_value=mcp_server.Path("/Users/alice")), \
            patch.dict(
                mcp_server.os.environ,
                {
                    "ULTRASTEALTH_USER_DATA_DIR": "/Users/alice/Library/Application Support/Chromium",
                    "ULTRASTEALTH_PROFILE_DIRECTORY": "Profile 6",
                },
            ):
            result = asyncio.run(mcp_server.browser_navigate("https://example.com"))

        self.assertIn("Navigated to: https://example.com", result)
        self.assertEqual(len(FakeFetcher.instances), 1)
        self.assertEqual(
            FakeFetcher.instances[0].user_data_dir,
            "/Users/alice/Library/Application Support/Chromium",
        )
        self.assertEqual(FakeFetcher.instances[0].profile_directory, "Profile 6")
        self.assertFalse(FakeFetcher.instances[0].fallback_to_temp_profile)

    def test_chrome_env_profile_config_disables_temp_fallback_for_first_launch(self):
        with patch.object(mcp_server, "UltrastealthFetcher", FakeFetcher), \
            patch.object(mcp_server.asyncio, "sleep", new=self._sleep), \
            patch.object(mcp_server.log, "info"), \
            patch.object(mcp_server.Path, "home", return_value=mcp_server.Path("/Users/alice")), \
            patch.dict(
                mcp_server.os.environ,
                {
                    "ULTRASTEALTH_RUNNER": "chrome+default-profile",
                    "ULTRASTEALTH_USER_DATA_DIR": "/Users/alice/Library/Application Support/Google/Chrome",
                    "ULTRASTEALTH_PROFILE_DIRECTORY": "Profile 6",
                },
            ):
            result = asyncio.run(mcp_server.browser_navigate("https://example.com"))

        self.assertIn("Navigated to: https://example.com", result)
        self.assertEqual(len(FakeFetcher.instances), 1)
        self.assertEqual(FakeFetcher.instances[0].runner, "chrome+default-profile")
        self.assertEqual(
            FakeFetcher.instances[0].user_data_dir,
            "/Users/alice/Library/Application Support/Google/Chrome",
        )
        self.assertEqual(FakeFetcher.instances[0].profile_directory, "Profile 6")
        self.assertFalse(FakeFetcher.instances[0].fallback_to_temp_profile)

    def test_no_arg_followup_reuses_active_profile_despite_env_profile(self):
        with patch.object(mcp_server, "UltrastealthFetcher", FakeFetcher), \
            patch.object(mcp_server.asyncio, "sleep", new=self._sleep), \
            patch.object(mcp_server.log, "info"), \
            patch.dict(
                mcp_server.os.environ,
                {
                    "ULTRASTEALTH_RUNNER": "chrome+default-profile",
                    "ULTRASTEALTH_USER_DATA_DIR": "/Users/alice/Library/Application Support/Google/Chrome",
                    "ULTRASTEALTH_PROFILE_DIRECTORY": "Profile 6",
                },
                clear=True,
            ):
            asyncio.run(
                mcp_server.browser_navigate(
                    "https://example.com/active",
                    runner="chrome+default-profile",
                    user_data_dir="/tmp/ultrastealth-isolated-profile",
                    profile_directory="Default",
                )
            )
            fetcher, page = asyncio.run(mcp_server._ensure_browser())

        self.assertEqual(len(FakeFetcher.instances), 1)
        self.assertIs(fetcher, FakeFetcher.instances[0])
        self.assertEqual(page.url, "https://example.com/active")
        self.assertEqual(
            FakeFetcher.instances[0].user_data_dir,
            "/tmp/ultrastealth-isolated-profile",
        )
        self.assertEqual(FakeFetcher.instances[0].profile_directory, "Default")

    def test_restart_without_profile_args_reuses_active_profile(self):
        with patch.object(mcp_server, "UltrastealthFetcher", FakeFetcher), \
            patch.object(mcp_server.asyncio, "sleep", new=self._sleep), \
            patch.object(mcp_server.log, "info"), \
            patch.dict(
                mcp_server.os.environ,
                {
                    "ULTRASTEALTH_RUNNER": "chrome+default-profile",
                    "ULTRASTEALTH_USER_DATA_DIR": "/Users/alice/Library/Application Support/Google/Chrome",
                    "ULTRASTEALTH_PROFILE_DIRECTORY": "Profile 6",
                },
                clear=True,
            ):
            asyncio.run(
                mcp_server.browser_navigate(
                    "https://example.com/active",
                    runner="chrome+default-profile",
                    user_data_dir="/tmp/ultrastealth-isolated-profile",
                    profile_directory="Default",
                )
            )
            result = asyncio.run(mcp_server.browser_restart())

        self.assertIn("Browser restarted. Navigated to: https://example.com/active", result)
        self.assertEqual(len(FakeFetcher.instances), 2)
        self.assertTrue(FakeFetcher.instances[0].closed)
        self.assertEqual(
            FakeFetcher.instances[1].user_data_dir,
            "/tmp/ultrastealth-isolated-profile",
        )
        self.assertEqual(FakeFetcher.instances[1].profile_directory, "Default")

    def test_temp_profile_runner_ignores_env_profile_config(self):
        with patch.object(mcp_server, "UltrastealthFetcher", FakeFetcher), \
            patch.object(mcp_server.asyncio, "sleep", new=self._sleep), \
            patch.object(mcp_server.log, "info"), \
            patch.dict(
                mcp_server.os.environ,
                {
                    "ULTRASTEALTH_RUNNER": "chrome+default-profile",
                    "ULTRASTEALTH_USER_DATA_DIR": "/Users/alice/Library/Application Support/Google/Chrome",
                    "ULTRASTEALTH_PROFILE_DIRECTORY": "Profile 6",
                },
                clear=True,
            ):
            result = asyncio.run(mcp_server.browser_restart(runner="chrome+temp-profile"))

        self.assertIn("Browser restarted with a clean session", result)
        self.assertEqual(FakeFetcher.instances[0].runner, "chrome+temp-profile")
        self.assertIsNone(FakeFetcher.instances[0].user_data_dir)
        self.assertIsNone(FakeFetcher.instances[0].profile_directory)

    def test_explicit_chrome_tool_path_is_preserved(self):
        with patch.object(mcp_server, "UltrastealthFetcher", FakeFetcher), \
            patch.object(mcp_server.asyncio, "sleep", new=self._sleep), \
            patch.object(mcp_server.log, "info"), \
            patch.object(mcp_server.Path, "home", return_value=mcp_server.Path("/Users/alice")):
            result = asyncio.run(
                mcp_server.browser_navigate(
                    "https://example.com",
                    user_data_dir="/Users/alice/Library/Application Support/Google/Chrome",
                    profile_directory="Profile 2",
                )
            )

        self.assertIn("Navigated to: https://example.com", result)
        self.assertEqual(len(FakeFetcher.instances), 1)
        self.assertEqual(
            FakeFetcher.instances[0].user_data_dir,
            "/Users/alice/Library/Application Support/Google/Chrome",
        )
        self.assertEqual(FakeFetcher.instances[0].profile_directory, "Profile 2")
        self.assertFalse(FakeFetcher.instances[0].fallback_to_temp_profile)

    def test_main_accepts_process_profile_options(self):
        with patch.dict(mcp_server.os.environ, {}, clear=True), \
            patch.object(
                mcp_server.sys,
                "argv",
                [
                    "ultrastealth-mcp",
                    "--transport",
                    "stdio",
                    "--runner",
                    "chromium+default-profile",
                    "--user-data-dir",
                    "/Users/alice/Library/Application Support/Chromium",
                    "--profile-directory",
                    "Profile 2",
                ],
            ), \
            patch.object(mcp_server.mcp, "run") as run:
            mcp_server.main()
            run.assert_called_once_with(transport="stdio")
            self.assertEqual(mcp_server.os.environ["ULTRASTEALTH_RUNNER"], "chromium+default-profile")
            self.assertEqual(
                mcp_server.os.environ["ULTRASTEALTH_USER_DATA_DIR"],
                "/Users/alice/Library/Application Support/Chromium",
            )
            self.assertEqual(mcp_server.os.environ["ULTRASTEALTH_PROFILE_DIRECTORY"], "Profile 2")

    def test_main_preserves_process_chrome_user_data_dir(self):
        with patch.dict(mcp_server.os.environ, {}, clear=True), \
            patch.object(mcp_server.Path, "home", return_value=mcp_server.Path("/Users/alice")), \
            patch.object(
                mcp_server.sys,
                "argv",
                [
                    "ultrastealth-mcp",
                    "--transport",
                    "stdio",
                    "--runner",
                    "chrome+default-profile",
                    "--user-data-dir",
                    "/Users/alice/Library/Application Support/Google/Chrome",
                    "--profile-directory",
                    "Profile 2",
                ],
            ), \
            patch.object(mcp_server.mcp, "run") as run:
            mcp_server.main()
            run.assert_called_once_with(transport="stdio")
            self.assertEqual(
                mcp_server.os.environ["ULTRASTEALTH_USER_DATA_DIR"],
                "/Users/alice/Library/Application Support/Google/Chrome",
            )
            self.assertEqual(mcp_server.os.environ["ULTRASTEALTH_RUNNER"], "chrome+default-profile")
            self.assertEqual(mcp_server.os.environ["ULTRASTEALTH_PROFILE_DIRECTORY"], "Profile 2")

    async def _sleep(self, delay):
        pass


if __name__ == "__main__":
    unittest.main()
