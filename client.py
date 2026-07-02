"""Client for the Ultrastealth warm-browser daemon."""
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def default_sock() -> str:
    d = Path(os.environ.get("ULTRASTEALTH_DAEMON_DIR", str(Path.home() / ".ultrastealth")))
    return str(d / "daemon.sock")


class DaemonError(Exception):
    def __init__(self, type_: str, message: str):
        super().__init__(message)
        self.type = type_
        self.message = message


class UltrastealthClient:
    """Attach to the warm daemon over its Unix socket.

    call() opens a short connection per request (cheap; the browser stays warm in
    the daemon). Auto-starts the daemon on first use unless autostart=False.
    """

    def __init__(self, sock: str | None = None, autostart: bool = True, timeout: float = 120.0):
        self.sock = sock or default_sock()
        self.autostart = autostart
        self.timeout = timeout
        self._id = 0

    def _ensure_daemon(self):
        if os.path.exists(self.sock):
            return
        if not self.autostart:
            raise DaemonError("no_daemon",
                              f"No daemon at {self.sock}; run `ultrastealth daemon start`")
        subprocess.Popen(
            [sys.executable, "-m", "ultrastealth.daemon", "run"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
        )
        for _ in range(100):  # up to ~10s for the socket to appear
            if os.path.exists(self.sock):
                return
            time.sleep(0.1)
        raise DaemonError("start_timeout", "Daemon did not become ready in time")

    async def call(self, cmd: str, **args):
        self._ensure_daemon()
        self._id += 1
        req = {"id": self._id, "cmd": cmd, "args": args}
        reader, writer = await asyncio.open_unix_connection(self.sock)
        try:
            writer.write((json.dumps(req) + "\n").encode())
            await writer.drain()
            line = await asyncio.wait_for(reader.readline(), timeout=self.timeout)
        finally:
            writer.close()
        resp = json.loads(line.decode())
        if not resp.get("ok"):
            err = resp.get("error", {})
            raise DaemonError(err.get("type", "error"), err.get("message", "unknown error"))
        return resp.get("result")


def connect(sock: str | None = None, autostart: bool = True) -> UltrastealthClient:
    """Attach to the warm daemon (starting it if needed). For scripts + MCP."""
    return UltrastealthClient(sock=sock, autostart=autostart)
