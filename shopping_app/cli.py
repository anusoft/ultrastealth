"""Command-line interface for the shopping crawler deployment."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import uuid
from pathlib import Path

from .database import (
    DEFAULT_DATABASE_URL,
    connect,
    create_run,
    health_snapshot,
    import_directory,
    schedule_failure,
    schedule_success,
    select_due_marketplace,
    set_run_state,
)
from .exporter import (
    create_baseline_export,
    create_incremental_export,
    import_incremental_bundle,
)
from .manifest import REQUIRED_SITES
from .migrations import apply_migrations
from .runner import marketplace_lock, run_marketplace


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="shopping")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("migrate", help="apply database migrations and seeds")

    ingest = subparsers.add_parser("ingest", help="ingest an existing crawl directory")
    ingest.add_argument("--site", required=True, choices=sorted(REQUIRED_SITES))
    ingest.add_argument("--path", required=True)
    ingest.add_argument("--mode", choices=("initial", "smoke", "full"), default="initial")
    ingest.add_argument("--run-id")

    crawl = subparsers.add_parser("crawl", help="run and ingest one marketplace")
    crawl.add_argument("site", choices=sorted(REQUIRED_SITES))
    crawl.add_argument("--mode", choices=("smoke", "full"), default="full")

    subparsers.add_parser("schedule", help="run the next due marketplace")
    subparsers.add_parser("health", help="print deployment health as JSON")
    subparsers.add_parser("queue-all", help="make all enabled marketplaces due")
    subparsers.add_parser("export-baseline", help="create a baseline pg_dump bundle")
    subparsers.add_parser("export-incremental", help="create an incremental bundle")

    import_bundle = subparsers.add_parser(
        "import-bundle", help="apply an incremental bundle idempotently"
    )
    import_bundle.add_argument("path")
    return parser


def _paths() -> tuple[Path, Path, str]:
    root = Path(os.environ.get("SHOPPING_ROOT", "/home/anu/shopping"))
    app_root = Path(os.environ.get("SHOPPING_APP_ROOT", str(root / "app")))
    bun_path = os.environ.get(
        "SHOPPING_BUN",
        "/var/lib/shopping/.bun/bin/bun",
    )
    return root, app_root, bun_path


def _print(value) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def initial_run_id(site: str, root: Path) -> str:
    """Return the repeatable run ID used for one historical import directory."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"shopping-initial:{site}:{root}"))


def active_lock_names(root: Path) -> list[str]:
    """Return local lock names whose recorded processes still exist."""
    active = []
    for path in sorted((root / "state" / "locks").glob("*.lock")):
        try:
            fields = dict(
                part.split("=", 1)
                for part in path.read_text(encoding="utf-8").split()
                if "=" in part
            )
            if fields.get("host") != socket.gethostname():
                continue
            os.kill(int(fields["pid"]), 0)
        except (FileNotFoundError, KeyError, ValueError, ProcessLookupError):
            continue
        except PermissionError:
            pass
        active.append(path.stem)
    return active


def systemd_state(unit: str) -> str:
    """Read a unit state without making health depend on systemd."""
    try:
        completed = subprocess.run(
            ["systemctl", "is-active", unit],
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return "unavailable"
    return completed.stdout.strip() or "unknown"


def command_migrate() -> int:
    with connect() as connection:
        installed = apply_migrations(connection)
        connection.commit()
    _print({"installed": installed})
    return 0


def command_ingest(args: argparse.Namespace) -> int:
    root = Path(args.path).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"ingest directory not found: {root}")
    run_id = args.run_id
    if run_id is None and args.mode == "initial":
        run_id = initial_run_id(args.site, root)
    with connect() as connection:
        run_id = create_run(
            connection,
            args.site,
            args.mode,
            str(root),
            run_id=run_id,
            state="succeeded",
        )
        connection.commit()
        counters = import_directory(connection, run_id, args.site, root)
    _print({"run_id": run_id, "site": args.site, **counters})
    return 0 if counters["errors"] == 0 else 2


def command_crawl(args: argparse.Namespace) -> int:
    root, app_root, bun_path = _paths()
    with connect() as connection:
        result = run_marketplace(
            connection,
            args.site,
            args.mode,
            root=root,
            app_root=app_root,
            bun_path=bun_path,
        )
    _print(result)
    return 0 if result["state"] == "imported" else 1


def command_schedule() -> int:
    root, app_root, bun_path = _paths()
    with marketplace_lock(root, "_scheduler"):
        with connect() as connection:
            slug = select_due_marketplace(connection)
            connection.commit()
            if slug is None:
                _print({"state": "idle", "reason": "no_due_marketplace"})
                return 0
            result = run_marketplace(
                connection,
                slug,
                "full",
                root=root,
                app_root=app_root,
                bun_path=bun_path,
            )
            if result["state"] == "imported":
                schedule_success(connection, slug)
            else:
                schedule_failure(connection, slug)
            connection.commit()
    _print({"site": slug, **result})
    return 0 if result["state"] == "imported" else 1


def command_health() -> int:
    root, _, _ = _paths()
    with connect() as connection:
        snapshot = health_snapshot(connection)
    snapshot["scheduler"] = {
        "timer": systemd_state("shopping-scheduler.timer"),
        "service": systemd_state("shopping-scheduler.service"),
    }
    snapshot["active_locks"] = active_lock_names(root)
    usage = shutil.disk_usage(root)
    snapshot["disk"] = {
        "total_bytes": usage.total,
        "used_bytes": usage.used,
        "free_bytes": usage.free,
    }
    _print(snapshot)
    return 0


def command_queue_all() -> int:
    with connect() as connection:
        changed = connection.execute(
            """
            UPDATE shopping.marketplaces
            SET next_crawl_at = now(), retry_count = 0, updated_at = now()
            WHERE enabled
            """
        ).rowcount
        connection.commit()
    _print({"queued": changed})
    return 0


def command_export_baseline() -> int:
    root, _, _ = _paths()
    database_url = os.environ.get("SHOPPING_DATABASE_URL", DEFAULT_DATABASE_URL)
    with connect(database_url) as connection:
        output = create_baseline_export(connection, root / "exports", database_url)
    _print({"state": "complete", "path": str(output)})
    return 0


def command_export_incremental() -> int:
    root, _, _ = _paths()
    with connect() as connection:
        output = create_incremental_export(connection, root / "exports")
    _print({"state": "complete", "path": str(output)})
    return 0


def command_import_bundle(args: argparse.Namespace) -> int:
    with connect() as connection:
        state = import_incremental_bundle(connection, Path(args.path).resolve())
    _print({"state": state, "path": str(Path(args.path).resolve())})
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    commands = {
        "migrate": lambda: command_migrate(),
        "ingest": lambda: command_ingest(args),
        "crawl": lambda: command_crawl(args),
        "schedule": lambda: command_schedule(),
        "health": lambda: command_health(),
        "queue-all": lambda: command_queue_all(),
        "export-baseline": lambda: command_export_baseline(),
        "export-incremental": lambda: command_export_incremental(),
        "import-bundle": lambda: command_import_bundle(args),
    }
    return commands[args.command]()


if __name__ == "__main__":
    sys.exit(main())
