"""Ultrastealth warm-browser daemon.

Owns a single browser_core instance and serves newline-delimited JSON-RPC over a
Unix socket. Exactly one process holds the CDP connection; the CLI, the MCP
server, and reusable scripts attach here instead of opening their own
connections (which is what causes reconnect churn / Network.enable timeouts).

Request:  {"id": int, "cmd": str, "args": {..}}
Response: {"id": int, "ok": true, "result": {..}}
          {"id": int, "ok": false, "error": {"type": str, "message": str}}
"""
import asyncio
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import browser_core
# Socket/pid/log paths are shared with the client so both derive the identical
# (length-safe) socket path from ULTRASTEALTH_DAEMON_DIR.
from client import daemon_dir, sock_path, pid_path, log_path  # noqa: F401

# Command registry: op name -> coroutine.
COMMANDS = dict(browser_core.OPS)
COMMANDS["ensure_browser"] = browser_core.ensure_browser
COMMANDS["close"] = browser_core.close

_LIFECYCLE = ("status", "ensure_browser", "close")
_last_activity = None


def _touch_idle():
    global _last_activity
    _last_activity = time.time()


async def dispatch(request: dict) -> dict:
    req_id = request.get("id")
    cmd = request.get("cmd")
    args = request.get("args") or {}
    fn = COMMANDS.get(cmd)
    if fn is None:
        return {"id": req_id, "ok": False,
                "error": {"type": "unknown_cmd", "message": f"Unknown command {cmd!r}"}}
    try:
        if cmd not in _LIFECYCLE and browser_core._page is None:
            await browser_core.ensure_browser()
        async with browser_core._op_lock:
            result = await fn(**args)
        return {"id": req_id, "ok": True, "result": result}
    except browser_core.BrowserCoreError as e:
        return {"id": req_id, "ok": False, "error": {"type": e.type, "message": e.message}}
    except Exception as e:  # noqa: BLE001
        return {"id": req_id, "ok": False, "error": {"type": "error", "message": str(e)}}


# ── Lifecycle (paths imported from client) ──────────────────────────
def read_pid() -> int | None:
    p = pid_path()
    if not p.exists():
        return None
    try:
        return int(p.read_text().strip())
    except ValueError:
        return None


def is_running() -> bool:
    pid = read_pid()
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


# ── Socket server ───────────────────────────────────────────────────
async def _handle_client(reader, writer):
    try:
        while True:
            line = await reader.readline()
            if not line:
                break
            try:
                request = json.loads(line.decode())
            except json.JSONDecodeError:
                writer.write((json.dumps({"ok": False,
                    "error": {"type": "bad_json", "message": "invalid JSON"}}) + "\n").encode())
                await writer.drain()
                continue
            resp = await dispatch(request)
            _touch_idle()
            writer.write((json.dumps(resp) + "\n").encode())
            await writer.drain()
    except (ConnectionResetError, asyncio.IncompleteReadError):
        pass
    finally:
        writer.close()


async def start_server(sock: str):
    if os.path.exists(sock):
        os.unlink(sock)
    server = await asyncio.start_unix_server(_handle_client, path=sock)
    os.chmod(sock, 0o600)
    return server


# ── Keep-warm + health watchdogs ────────────────────────────────────
async def _idle_reaper(idle_timeout: float):
    """Close the browser after inactivity; keep the daemon listening."""
    if idle_timeout <= 0:
        return
    while True:
        await asyncio.sleep(min(idle_timeout, 30))
        if _last_activity and (time.time() - _last_activity) > idle_timeout:
            if browser_core._page is not None:
                await browser_core.close()


async def _health_watchdog(interval: float = 20.0):
    """Ping the page; hard-restart a wedged browser."""
    while True:
        await asyncio.sleep(interval)
        if browser_core._page is None:
            continue
        try:
            await asyncio.wait_for(browser_core._page.title(), timeout=10)
        except Exception:
            await browser_core.close()  # next op re-launches clean


async def run(idle_timeout: float = 1800.0):
    sock = sock_path()
    pid_path().write_text(str(os.getpid()))
    _touch_idle()
    server = await start_server(sock)
    reaper = asyncio.create_task(_idle_reaper(idle_timeout))
    watchdog = asyncio.create_task(_health_watchdog())
    try:
        async with server:
            await server.serve_forever()
    finally:
        reaper.cancel()
        watchdog.cancel()
        await browser_core.close()
        pid_path().unlink(missing_ok=True)
        if os.path.exists(sock):
            os.unlink(sock)


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if argv and argv[0] == "run":
        idle = float(os.environ.get("ULTRASTEALTH_IDLE_TIMEOUT", "1800"))
        asyncio.run(run(idle_timeout=idle))
    else:
        print("usage: python -m ultrastealth.daemon run", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
