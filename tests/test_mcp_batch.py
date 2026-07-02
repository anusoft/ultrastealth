import asyncio
import json
import os
import unittest
import unittest.mock
from types import SimpleNamespace

import mcp_server
from tests.browser_fakes import FakePage, FakeContext


class McpBatchTests(unittest.TestCase):
    def setUp(self):
        self.page = FakePage()
        self.context = FakeContext(self.page)
        mcp_server._fetcher = SimpleNamespace(_context=self.context, user_data_dir=None)
        mcp_server._page = self.page
        mcp_server._browser_wedged = False
        mcp_server._browser_config = (None, None, None)
        # Force the owning path deterministically (ignore any real daemon socket).
        os.environ["ULTRASTEALTH_MCP_NO_DAEMON"] = "1"
        self.addCleanup(os.environ.pop, "ULTRASTEALTH_MCP_NO_DAEMON", None)

    def test_browser_batch_runs_steps(self):
        steps = [{"op": "navigate", "url": "https://example.com/x", "wait_secs": 0},
                 {"op": "snapshot"}]
        out = asyncio.run(mcp_server.browser_batch(json.dumps(steps)))
        self.assertIn("navigate", out)
        self.assertEqual(self.page.goto_calls[-1][0], "https://example.com/x")

    def test_browser_snapshot_emits_e_refs(self):
        out = asyncio.run(mcp_server.browser_snapshot())
        self.assertIn("[e0]", out)
        self.assertIn("Submit", out)

    def test_uses_daemon_when_socket_present(self):
        recorded = {}

        class FakeClient:
            def __init__(self, **kw):
                pass

            async def call(self, cmd, **args):
                recorded["cmd"] = cmd
                recorded["args"] = args
                return {"steps": []}

        with unittest.mock.patch.object(mcp_server, "_daemon_available", lambda: True), \
             unittest.mock.patch.object(mcp_server, "UltrastealthClient", FakeClient):
            out = asyncio.run(mcp_server.browser_batch('[{"op":"snapshot"}]'))
        self.assertEqual(recorded["cmd"], "batch")
        self.assertIn("steps", out)


if __name__ == "__main__":
    unittest.main()
