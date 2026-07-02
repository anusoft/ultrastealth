import asyncio
import json
import tempfile
import unittest
from pathlib import Path

import client


class ClientTests(unittest.TestCase):
    def test_call_sends_request_and_parses_result(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                sock = str(Path(tmp) / "d.sock")

                async def handle(reader, writer):
                    line = await reader.readline()
                    req = json.loads(line.decode())
                    resp = {"id": req["id"], "ok": True, "result": {"echo": req["args"]}}
                    writer.write((json.dumps(resp) + "\n").encode())
                    await writer.drain()
                    writer.close()

                server = await asyncio.start_unix_server(handle, path=sock)
                c = client.UltrastealthClient(sock=sock, autostart=False)
                res = await c.call("click", target="e2")
                server.close()
                await server.wait_closed()
                return res

        self.assertEqual(asyncio.run(scenario()), {"echo": {"target": "e2"}})

    def test_call_raises_on_error_response(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                sock = str(Path(tmp) / "d.sock")

                async def handle(reader, writer):
                    line = await reader.readline()
                    req = json.loads(line.decode())
                    resp = {"id": req["id"], "ok": False,
                            "error": {"type": "stale_ref", "message": "gone"}}
                    writer.write((json.dumps(resp) + "\n").encode())
                    await writer.drain()
                    writer.close()

                server = await asyncio.start_unix_server(handle, path=sock)
                c = client.UltrastealthClient(sock=sock, autostart=False)
                try:
                    await c.call("click", target="e2")
                finally:
                    server.close()
                    await server.wait_closed()

        with self.assertRaises(client.DaemonError) as ctx:
            asyncio.run(scenario())
        self.assertEqual(ctx.exception.type, "stale_ref")

    def test_no_autostart_raises_when_no_daemon(self):
        c = client.UltrastealthClient(sock="/nonexistent/does-not-exist.sock", autostart=False)
        with self.assertRaises(client.DaemonError) as ctx:
            asyncio.run(c.call("status"))
        self.assertEqual(ctx.exception.type, "no_daemon")


if __name__ == "__main__":
    unittest.main()
