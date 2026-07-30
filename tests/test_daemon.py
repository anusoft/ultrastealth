import asyncio
import contextlib
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


class DispatchSessionLockTests(unittest.TestCase):
    """dispatch() is supposed to thread request["session"] through to
    browser_core.get_op_lock so a future multi-session daemon can serialize
    per-session instead of globally. Verify the actual concurrency behavior
    (not just that get_op_lock was called with some argument): two dispatches
    on the *same* session must serialize, two on *different* sessions must
    not block each other.
    """

    def setUp(self):
        browser_core.reset_state_for_tests(fetcher=make_fetcher(FakePage()), page=FakePage())
        self.order = []
        self.release = asyncio.Event()

        async def slow(**kw):
            self.order.append("start-slow")
            await self.release.wait()
            self.order.append("end-slow")
            return {}

        async def fast(**kw):
            self.order.append("start-fast")
            return {}

        self.slow = slow
        self.fast = fast

    def test_same_session_serializes(self):
        async def scenario():
            with unittest.mock.patch.dict(
                    daemon.COMMANDS, {"slow": self.slow, "fast": self.fast}, clear=False):
                t1 = asyncio.create_task(
                    daemon.dispatch({"id": 1, "cmd": "slow", "args": {}, "session": "s1"}))
                await asyncio.sleep(0.02)
                t2 = asyncio.create_task(
                    daemon.dispatch({"id": 2, "cmd": "fast", "args": {}, "session": "s1"}))
                await asyncio.sleep(0.02)
                # "fast" must still be waiting on s1's lock, held by "slow".
                self.assertNotIn("start-fast", self.order)
                self.release.set()
                await t1
                await t2

        asyncio.run(scenario())
        self.assertEqual(self.order, ["start-slow", "end-slow", "start-fast"])

    def test_different_sessions_do_not_block_each_other(self):
        async def scenario():
            with unittest.mock.patch.dict(
                    daemon.COMMANDS, {"slow": self.slow, "fast": self.fast}, clear=False):
                t1 = asyncio.create_task(
                    daemon.dispatch({"id": 1, "cmd": "slow", "args": {}, "session": "s1"}))
                await asyncio.sleep(0.02)
                t2 = asyncio.create_task(
                    daemon.dispatch({"id": 2, "cmd": "fast", "args": {}, "session": "s2"}))
                await asyncio.sleep(0.02)
                # "fast" on a different session must run immediately, before
                # "slow" (s1) releases -- proving distinct per-session locks.
                self.assertIn("start-fast", self.order)
                self.assertNotIn("end-slow", self.order)
                self.release.set()
                await t1
                await t2

        asyncio.run(scenario())
        self.assertEqual(self.order, ["start-slow", "start-fast", "end-slow"])

    def test_missing_session_defaults_to_shared_default_lock(self):
        """No client sends `session` today; omitting it must still serialize
        against other default-session callers (the pre-refactor behavior),
        not silently become unserialized.
        """
        async def scenario():
            with unittest.mock.patch.dict(
                    daemon.COMMANDS, {"slow": self.slow, "fast": self.fast}, clear=False):
                t1 = asyncio.create_task(
                    daemon.dispatch({"id": 1, "cmd": "slow", "args": {}}))
                await asyncio.sleep(0.02)
                t2 = asyncio.create_task(
                    daemon.dispatch({"id": 2, "cmd": "fast", "args": {}}))
                await asyncio.sleep(0.02)
                self.assertNotIn("start-fast", self.order)
                self.release.set()
                await t1
                await t2

        asyncio.run(scenario())
        self.assertEqual(self.order, ["start-slow", "end-slow", "start-fast"])


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


class HealthWatchdogTests(unittest.TestCase):
    def setUp(self):
        browser_core.reset_state_for_tests(fetcher=make_fetcher(FakePage()), page=FakePage())
        daemon._last_health_state = None

    def _run_one_tick(self, diag):
        async def scenario():
            with unittest.mock.patch.object(
                    browser_core, "health_check", unittest.mock.AsyncMock(return_value=diag)), \
                 unittest.mock.patch.object(
                    browser_core, "close", unittest.mock.AsyncMock()) as close_mock:
                task = asyncio.create_task(daemon._health_watchdog(interval=0.01))
                await asyncio.sleep(0.05)
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
                return close_mock

        return asyncio.run(scenario())

    def test_healthy_never_closes(self):
        close_mock = self._run_one_tick({"state": "healthy", "process_alive": True, "cdp_ok": True})
        close_mock.assert_not_called()
        self.assertEqual(daemon._last_health_state, "healthy")

    def test_process_exited_closes(self):
        close_mock = self._run_one_tick(
            {"state": "process_exited", "process_alive": False, "cdp_ok": False})
        close_mock.assert_called()
        self.assertEqual(daemon._last_health_state, "process_exited")

    def test_unresponsive_closes(self):
        close_mock = self._run_one_tick(
            {"state": "unresponsive", "process_alive": True, "cdp_ok": False})
        close_mock.assert_called()
        self.assertEqual(daemon._last_health_state, "unresponsive")

    def test_no_browser_error_is_skipped_without_closing(self):
        async def raise_no_browser(**kw):
            raise browser_core.BrowserCoreError("no_browser", "Browser is not started")

        async def scenario():
            with unittest.mock.patch.object(browser_core, "health_check", raise_no_browser), \
                 unittest.mock.patch.object(
                    browser_core, "close", unittest.mock.AsyncMock()) as close_mock:
                task = asyncio.create_task(daemon._health_watchdog(interval=0.01))
                await asyncio.sleep(0.05)
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
                return close_mock

        close_mock = asyncio.run(scenario())
        close_mock.assert_not_called()

    def test_unexpected_exception_from_health_check_does_not_kill_watchdog(self):
        """Regression: the watchdog used to catch only BrowserCoreError, so any
        other exception out of health_check() (an unusual psutil failure, a
        future bug, anything not BrowserCoreError) escaped the `while True`
        loop and permanently ended the background task -- since nothing
        restarts it, that silently disabled health monitoring for the rest of
        the daemon's life. The task must still be alive and ticking after an
        unexpected failure.
        """
        calls = {"n": 0}

        async def flaky_health_check(**kw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("unexpected psutil-ish failure")
            return {"state": "healthy", "process_alive": True, "cdp_ok": True}

        async def scenario():
            with unittest.mock.patch.object(browser_core, "health_check", flaky_health_check), \
                 unittest.mock.patch.object(browser_core, "close", unittest.mock.AsyncMock()):
                task = asyncio.create_task(daemon._health_watchdog(interval=0.01))
                await asyncio.sleep(0.05)
                still_running = not task.done()
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
                return still_running

        still_running = asyncio.run(scenario())
        self.assertTrue(still_running,
                         "an unexpected exception must not permanently kill the watchdog task")
        self.assertGreaterEqual(calls["n"], 2, "watchdog must keep ticking after the failure")
        self.assertEqual(daemon._last_health_state, "healthy")

    def test_unexpected_exception_from_close_does_not_kill_watchdog(self):
        async def flaky_close():
            raise RuntimeError("close blew up")

        async def scenario():
            with unittest.mock.patch.object(
                    browser_core, "health_check", unittest.mock.AsyncMock(
                        return_value={"state": "process_exited", "process_alive": False, "cdp_ok": False})), \
                 unittest.mock.patch.object(browser_core, "close", flaky_close):
                task = asyncio.create_task(daemon._health_watchdog(interval=0.01))
                await asyncio.sleep(0.05)
                still_running = not task.done()
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
                return still_running

        still_running = asyncio.run(scenario())
        self.assertTrue(still_running,
                         "an unexpected exception from close() must not permanently kill the watchdog task")


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
