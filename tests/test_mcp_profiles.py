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

    async def _sleep(self, delay):
        pass


if __name__ == "__main__":
    unittest.main()
