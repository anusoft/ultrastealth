"""Run Bun crawlers with disk guards, locks, resume, and atomic finalization."""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import socket
import subprocess
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .database import create_run, import_directory, set_run_state
from .manifest import load_manifest, run_args


MIN_FREE_BYTES = 10 * 1024**3


def ensure_disk_space(free_bytes: int) -> None:
    """Reject a crawl when less than 10 GiB remains."""
    if free_bytes < MIN_FREE_BYTES:
        raise RuntimeError(
            f"crawl requires 10 GiB free; found {free_bytes} bytes"
        )


def partial_directory(root: Path, slug: str) -> Path:
    """Create or reopen the current resumable directory for a marketplace."""
    partial = root / "partial" / slug / "current"
    partial.mkdir(parents=True, exist_ok=True)
    run_file = partial / ".run-id"
    if not run_file.exists():
        run_file.write_text(f"{uuid.uuid4()}\n", encoding="utf-8")
    return partial


def run_id_for_partial(partial: Path) -> str:
    """Read and validate the UUID attached to a partial directory."""
    value = (partial / ".run-id").read_text(encoding="utf-8").strip()
    return str(uuid.UUID(value))


def build_crawl_command(
    site: dict[str, Any],
    mode: str,
    app_root: Path,
    output: Path,
    bun_path: str,
) -> list[str]:
    """Build one explicit Bun command without relying on crawler defaults."""
    return [
        bun_path,
        "run",
        str(app_root / site["entrypoint"]),
        *run_args(site, mode),
        "--out",
        str(output),
    ]


def finalize_partial(root: Path, slug: str, partial: Path) -> Path:
    """Atomically rename a successful partial directory into immutable data."""
    run_id = run_id_for_partial(partial)
    destination = root / "data" / slug / run_id
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError(f"final crawl directory already exists: {destination}")
    partial.rename(destination)
    return destination


@contextmanager
def marketplace_lock(root: Path, slug: str) -> Iterator[None]:
    """Hold a non-blocking exclusive lock for one marketplace."""
    lock_path = root / "state" / "locks" / f"{slug}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(f"marketplace crawl already running: {slug}") from exc
        lock_file.seek(0)
        lock_file.truncate()
        lock_file.write(f"pid={os.getpid()} host={socket.gethostname()}\n")
        lock_file.flush()
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def run_marketplace(
    connection: Any,
    slug: str,
    mode: str,
    *,
    root: Path,
    app_root: Path,
    bun_path: str,
) -> dict[str, Any]:
    """Execute, finalize, and import one marketplace crawl."""
    manifest = load_manifest()
    if slug not in manifest:
        raise ValueError(f"unknown marketplace: {slug}")
    ensure_disk_space(shutil.disk_usage(root).free)
    site = manifest[slug]

    with marketplace_lock(root, slug):
        partial = partial_directory(root, slug)
        run_id = run_id_for_partial(partial)
        command = build_crawl_command(site, mode, app_root, partial, bun_path)
        create_run(
            connection,
            slug,
            mode,
            str(partial),
            command_args=command,
            run_id=run_id,
        )
        set_run_state(connection, run_id, "running")
        connection.commit()

        started_at = datetime.now(timezone.utc)
        completed = subprocess.run(command, cwd=app_root, check=False)
        if completed.returncode != 0:
            error = {
                "returncode": completed.returncode,
                "command": command,
            }
            set_run_state(connection, run_id, "failed", error=error)
            connection.commit()
            return {"run_id": run_id, "state": "failed", "error": error}

        run_manifest = {
            "run_id": run_id,
            "marketplace": slug,
            "mode": mode,
            "host": socket.gethostname(),
            "command": command,
            "started_at": started_at.isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }
        (partial / "run-manifest.json").write_text(
            json.dumps(run_manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        final = finalize_partial(root, slug, partial)
        set_run_state(
            connection,
            run_id,
            "succeeded",
            output_path=str(final),
        )
        counters = import_directory(connection, run_id, slug, final)
        connection.commit()
        return {
            "run_id": run_id,
            "state": "imported" if counters["errors"] == 0 else "succeeded",
            "output_path": str(final),
            "counters": counters,
        }
