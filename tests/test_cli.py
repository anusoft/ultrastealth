import unittest

import cli


class FakeClient:
    def __init__(self, **kw):
        self.kw = kw
        self.calls = []

    async def call(self, cmd, **args):
        self.calls.append((cmd, args))
        return {"ok": True, "cmd": cmd, "args": args}


class CliParseTests(unittest.TestCase):
    def _run(self, argv):
        fc = FakeClient()
        cli.run_argv(argv, client_factory=lambda **kw: fc)
        return fc.calls

    def test_navigate_maps_to_call(self):
        calls = self._run(["browser", "navigate", "https://example.com"])
        self.assertEqual(calls[0][0], "navigate")
        self.assertEqual(calls[0][1]["url"], "https://example.com")

    def test_click_with_snapshot_after(self):
        calls = self._run(["browser", "click", "e2", "--snapshot-after"])
        self.assertEqual(calls[0], ("click", {"target": "e2", "snapshot_after": True}))

    def test_type_with_text(self):
        calls = self._run(["browser", "type", "#email", "--text", "a@b.com"])
        self.assertEqual(calls[0], ("type", {"target": "#email", "text": "a@b.com"}))

    def test_wait_selector(self):
        calls = self._run(["browser", "wait", "--selector", "#ready", "--timeout-ms", "5000"])
        self.assertEqual(calls[0], ("wait", {"selector": "#ready", "timeout_ms": 5000}))

    def test_snapshot_flags(self):
        calls = self._run(["browser", "snapshot", "--interactive", "--compact", "--diff"])
        self.assertEqual(calls[0], ("snapshot",
                          {"interactive": True, "compact": True, "diff": True}))

    def test_back_maps_to_go_back(self):
        calls = self._run(["browser", "back"])
        self.assertEqual(calls[0], ("go_back", {}))

    def test_scroll_into_view_alias(self):
        calls = self._run(["browser", "scroll-into-view", "#el"])
        self.assertEqual(calls[0], ("scroll_into_view", {"target": "#el"}))

    def test_url_maps_to_get(self):
        calls = self._run(["browser", "url"])
        self.assertEqual(calls[0], ("get", {"kind": "url"}))

    def test_cookies_maps_to_call(self):
        calls = self._run(["browser", "cookies"])
        self.assertEqual(calls[0], ("cookies", {}))

    def test_find_maps_to_call_with_query(self):
        calls = self._run(["browser", "find", "login button"])
        self.assertEqual(calls[0], ("find", {"query": "login button"}))


if __name__ == "__main__":
    unittest.main()
