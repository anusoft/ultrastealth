"""Ultrastealth CLI — drives the warm-browser daemon (cmux-style).

Examples:
    ultrastealth daemon start
    ultrastealth browser navigate https://example.com
    ultrastealth browser snapshot --interactive --compact
    ultrastealth browser click e2 --snapshot-after
    ultrastealth browser type e5 --text "hello"
    ultrastealth browser batch steps.json
"""
import argparse
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from client import UltrastealthClient  # noqa: E402

# CLI op name -> core command name (when they differ).
_OP_ALIASES = {"back": "go_back", "scroll-into-view": "scroll_into_view"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ultrastealth", description="Ultrastealth warm browser")
    parser.add_argument("--socket", default=None, help="daemon socket path")
    parser.add_argument("--json", action="store_true", help="raw JSON output")
    parser.add_argument("--no-autostart", action="store_true", help="fail if no daemon is running")
    sub = parser.add_subparsers(dest="group", required=True)

    dp = sub.add_parser("daemon", help="manage the daemon")
    dsub = dp.add_subparsers(dest="action", required=True)
    for a in ("start", "stop", "status", "logs", "run"):
        dsub.add_parser(a)

    bp = sub.add_parser("browser", help="drive the browser")
    b = bp.add_subparsers(dest="op", required=True)

    def mut(name, target=False):
        p = b.add_parser(name)
        if target:
            p.add_argument("target", help="element ref (e2) or CSS selector")
        p.add_argument("--snapshot-after", action="store_true")
        return p

    nav = mut("navigate"); nav.add_argument("url"); nav.add_argument("--wait-secs", type=float)
    mut("back"); mut("reload")
    b.add_parser("url"); b.add_parser("title")

    snap = b.add_parser("snapshot")
    snap.add_argument("--interactive", action="store_true")
    snap.add_argument("--compact", action="store_true")
    snap.add_argument("--diff", action="store_true")

    for name in ("click", "hover", "focus", "scroll-into-view"):
        mut(name, target=True)
    typ = mut("type", target=True); typ.add_argument("--text", required=True)
    typ.add_argument("--submit", action="store_true")
    fil = mut("fill", target=True); fil.add_argument("--text", required=True)
    selp = mut("select", target=True); selp.add_argument("--value", required=True)
    prs = mut("press"); prs.add_argument("key")
    scr = mut("scroll"); scr.add_argument("--direction", default="down")
    scr.add_argument("--amount", type=int, default=500)

    w = b.add_parser("wait")
    w.add_argument("--selector"); w.add_argument("--text"); w.add_argument("--url-contains")
    w.add_argument("--load-state"); w.add_argument("--function"); w.add_argument("--timeout-ms", type=int)

    g = b.add_parser("get"); g.add_argument("kind"); g.add_argument("target", nargs="?")
    g.add_argument("--attribute")
    iss = b.add_parser("is"); iss.add_argument("kind"); iss.add_argument("target")
    ev = b.add_parser("eval"); ev.add_argument("javascript")
    sh = b.add_parser("screenshot"); sh.add_argument("--out"); sh.add_argument("--full-page", action="store_true")
    ba = b.add_parser("batch"); ba.add_argument("file", help="JSON file of steps, or - for stdin")
    return parser


def _op_to_call(op: str, args) -> tuple[str, dict]:
    if op == "navigate":
        kw = {"url": args.url}
        if args.wait_secs is not None:
            kw["wait_secs"] = args.wait_secs
        return "navigate", kw
    if op in ("back", "reload"):
        return _OP_ALIASES.get(op, op), {}
    if op == "url":
        return "get", {"kind": "url"}
    if op == "title":
        return "get", {"kind": "title"}
    if op == "snapshot":
        return "snapshot", {"interactive": args.interactive, "compact": args.compact, "diff": args.diff}
    if op in ("click", "hover", "focus", "scroll-into-view"):
        return _OP_ALIASES.get(op, op), {"target": args.target}
    if op == "type":
        kw = {"target": args.target, "text": args.text}
        if args.submit:
            kw["submit"] = True
        return "type", kw
    if op == "fill":
        return "fill", {"target": args.target, "text": args.text}
    if op == "select":
        return "select", {"target": args.target, "value": args.value}
    if op == "press":
        return "press", {"key": args.key}
    if op == "scroll":
        return "scroll", {"direction": args.direction, "amount": args.amount}
    if op == "wait":
        kw = {}
        for attr, dst in (("selector", "selector"), ("text", "text"),
                          ("url_contains", "url_contains"), ("load_state", "load_state"),
                          ("function", "javascript")):
            v = getattr(args, attr, None)
            if v is not None:
                kw[dst] = v
        if args.timeout_ms is not None:
            kw["timeout_ms"] = args.timeout_ms
        return "wait", kw
    if op == "get":
        kw = {"kind": args.kind}
        if args.target:
            kw["target"] = args.target
        if args.attribute:
            kw["attribute"] = args.attribute
        return "get", kw
    if op == "is":
        return "is", {"kind": args.kind, "target": args.target}
    if op == "eval":
        return "evaluate", {"javascript": args.javascript}
    if op == "screenshot":
        kw = {"full_page": args.full_page}
        if args.out:
            kw["path"] = args.out
        return "screenshot", kw
    raise SystemExit(f"unhandled op: {op}")


def _emit(result, as_json):
    if isinstance(result, (dict, list)):
        print(json.dumps(result, indent=2))
    else:
        print(result)


def _handle_daemon(args):
    import daemon
    if args.action == "run":
        idle = float(os.environ.get("ULTRASTEALTH_IDLE_TIMEOUT", "1800"))
        return asyncio.run(daemon.run(idle_timeout=idle))
    if args.action == "start":
        if daemon.is_running():
            print("daemon already running")
            return
        logf = open(daemon.log_path(), "a")
        subprocess.Popen([sys.executable, "-m", "ultrastealth.cli", "daemon", "run"],
                         stdout=logf, stderr=logf, start_new_session=True)
        print(f"daemon starting; socket {daemon.sock_path()}")
        return
    if args.action == "stop":
        pid = daemon.read_pid()
        if pid:
            os.kill(pid, 15)
            print(f"stopped daemon pid {pid}")
        else:
            print("no daemon running")
        return
    if args.action == "status":
        print(json.dumps({"running": daemon.is_running(), "socket": daemon.sock_path(),
                          "pid": daemon.read_pid()}, indent=2))
        return
    if args.action == "logs":
        p = daemon.log_path()
        print(p.read_text() if p.exists() else "(no log yet)")
        return


def run_argv(argv, client_factory=UltrastealthClient):
    parser = build_parser()
    args = parser.parse_args(argv)
    kw = {}
    if args.socket:
        kw["sock"] = args.socket
    if args.no_autostart:
        kw["autostart"] = False

    if args.group == "daemon":
        return _handle_daemon(args)

    if args.op == "batch":
        text = sys.stdin.read() if args.file == "-" else Path(args.file).read_text()
        steps = json.loads(text)
        c = client_factory(**kw)
        result = asyncio.run(c.call("batch", steps=steps))
    else:
        cmd, call_kw = _op_to_call(args.op, args)
        if getattr(args, "snapshot_after", False):
            call_kw["snapshot_after"] = True
        c = client_factory(**kw)
        result = asyncio.run(c.call(cmd, **call_kw))
    _emit(result, args.json)
    return result


def main():
    run_argv(sys.argv[1:])


if __name__ == "__main__":
    main()
