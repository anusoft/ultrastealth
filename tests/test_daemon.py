import asyncio
import json
import os
import tempfile
import unittest
import unittest.mock
from pathlib import Path

import browser_core
import daemon
from tests.browser_fakes import FakePage, make_fetcher


class DispatchTests(unittest.TestCase):
    def setUp(self):
        # Inject a warm fake so dispatch never launches a real browser.
        browser_core.reset_state_for_tests(fetcher=make_fetcher(FakePage()), page=FakePage())

    def test_dispatch_success(self):
        async def fake_status():
            return {"warm": True}

        with unittest.mock.patch.dict(daemon.COMMANDS, {"status": fake_status}, clear=False):
            resp = asyncio.run(daemon.dispatch({"id": 1, "cmd": "status", "args": {}}))
        self.assertEqual(resp, {"id": 1, "ok": True, "result": {"warm": True}})

    def test_dispatch_unknown_cmd(self):
        resp = asyncio.run(daemon.dispatch({"id": 2, "cmd": "nope", "args": {}}))
        self.assertFalse(resp["ok"])
        self.assertEqual(resp["error"]["type"], "unknown_cmd")

    def test_dispatch_core_error(self):
        async def boom(**kw):
            raise browser_core.BrowserCoreError("stale_ref", "gone")

        with unittest.mock.patch.dict(daemon.COMMANDS, {"click": boom}, clear=False):
            resp = asyncio.run(daemon.dispatch({"id": 3, "cmd": "click", "args": {"target": "e9"}}))
        self.assertEqual(resp["error"]["type"], "stale_ref")


class SocketServerTests(unittest.TestCase):
    def test_socket_round_trip(self):
        async def scenario():
            async def fake_status():
                return {"warm": True, "url": "https://x"}

            with tempfile.TemporaryDirectory() as tmp:
                sock = str(Path(tmp) / "d.sock")
                with unittest.mock.patch.dict(daemon.COMMANDS, {"status": fake_status}, clear=False):
                    server = await daemon.start_server(sock)
                    reader, writer = await asyncio.open_unix_connection(sock)
                    writer.write((json.dumps({"id": 9, "cmd": "status", "args": {}}) + "\n").encode())
                    await writer.drain()
                    line = await reader.readline()
                    writer.close()
                    server.close()
                    await server.wait_closed()
                    return json.loads(line)

        resp = asyncio.run(scenario())
        self.assertEqual(resp["id"], 9)
        self.assertTrue(resp["result"]["warm"])


class LifecycleTests(unittest.TestCase):
    def test_read_pid_missing_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            with unittest.mock.patch.dict(os.environ, {"ULTRASTEALTH_DAEMON_DIR": tmp}):
                self.assertIsNone(daemon.read_pid())

    def test_is_running_false_for_dead_pid(self):
        with tempfile.TemporaryDirectory() as tmp:
            with unittest.mock.patch.dict(os.environ, {"ULTRASTEALTH_DAEMON_DIR": tmp}):
                daemon.pid_path().write_text("999999")
                self.assertFalse(daemon.is_running())

    def test_is_running_true_for_current_pid(self):
        with tempfile.TemporaryDirectory() as tmp:
            with unittest.mock.patch.dict(os.environ, {"ULTRASTEALTH_DAEMON_DIR": tmp}):
                daemon.pid_path().write_text(str(os.getpid()))
                self.assertTrue(daemon.is_running())


if __name__ == "__main__":
    unittest.main()
