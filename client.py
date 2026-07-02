"""Client for the Ultrastealth warm-browser daemon."""
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


import hashlib
import tempfile

# AF_UNIX socket paths are limited to ~104 bytes (sun_path) on macOS/Linux. pid
# and log files have no such limit, so only the socket needs the short fallback.
_SUN_PATH_MAX = 103


def daemon_dir() -> Path:
    d = Path(os.environ.get("ULTRASTEALTH_DAEMON_DIR", str(Path.home() / ".ultrastealth")))
    d.mkdir(parents=True, exist_ok=True)
    return d


def sock_path() -> str:
    """Socket path. Falls back to a short temp path when the configured daemon
    dir would exceed the AF_UNIX length limit (daemon and client agree because
    both derive it the same way from the daemon dir)."""
    p = str(daemon_dir() / "daemon.sock")
    if len(p) <= _SUN_PATH_MAX:
        return p
    h = hashlib.sha1(str(daemon_dir()).encode()).hexdigest()[:8]
    return str(Path(tempfile.gettempdir()) / f"ultrastealth-{h}.sock")


def pid_path() -> Path:
    return daemon_dir() / "daemon.pid"


def log_path() -> Path:
    return daemon_dir() / "daemon.log"


def default_sock() -> str:
    return sock_path()


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
        # Capture the daemon's startup output so a crash is diagnosable (not
        # silently swallowed) — this is what makes start failures debuggable.
        logf = open(log_path(), "a")
        subprocess.Popen(
            [sys.executable, "-m", "ultrastealth.daemon", "run"],
            stdout=logf, stderr=logf, start_new_session=True,
        )
        for _ in range(100):  # up to ~10s for the socket to appear
            if os.path.exists(self.sock):
                return
            time.sleep(0.1)
        tail = ""
        try:
            tail = "\n".join(log_path().read_text().splitlines()[-12:])
        except OSError:
            pass
        raise DaemonError("start_timeout",
                          f"Daemon did not become ready in time. Log tail:\n{tail}")

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
