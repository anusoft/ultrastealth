"""Run Bun crawlers with disk guards, locks, resume, and atomic finalization."""

from __future__ import annotations

import fcntl
import json
import os
import shutil
import socket
import subprocess
import sys
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


def recoverable_final_directory(
    connection: Any,
    root: Path,
    slug: str,
) -> tuple[str, Path] | None:
    """Find a finalized run whose database import did not complete."""
    rows = connection.execute(
        """
        SELECT r.id
        FROM shopping.crawl_runs r
        JOIN shopping.marketplaces m ON m.id = r.marketplace_id
        WHERE m.slug = %s AND r.state <> 'imported'
        ORDER BY r.created_at DESC
        """,
        (slug,),
    ).fetchall()
    for row in rows:
        run_id = str(row[0])
        final = root / "data" / slug / run_id
        if final.is_dir():
            return run_id, final
    return None


def _first_integer(summary: dict[str, Any], *keys: str) -> int | None:
    """Return the first integer-valued summary field from known aliases."""
    for key in keys:
        value = summary.get(key)
        if isinstance(value, int):
            return value
    return None


def validate_crawl_output(root: Path, mode: str = "full") -> int:
    """Reject empty or internally incomplete successful crawler output."""
    product_files = [
        path
        for path in root.rglob("*.json")
        if "products" in path.relative_to(root).parts
    ]
    if not product_files:
        raise RuntimeError(f"crawl produced no product JSON files in {root}")
    if mode == "full":
        for path in sorted(root.rglob("*.json")):
            if path.name not in {"summary.json", "run-summary.json"}:
                continue
            try:
                summary = json.loads(path.read_text(encoding="utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise RuntimeError(f"invalid crawl summary {path}: {exc}") from exc
            if not isinstance(summary, dict):
                raise RuntimeError(f"crawl summary is not an object: {path}")
            category_count = _first_integer(
                summary,
                "categoryCount",
                "departmentsDiscovered",
                "categoriesDiscovered",
            )
            categories_crawled = _first_integer(
                summary,
                "categoriesCrawled",
                "departmentsSelected",
                "categoriesSelected",
            )
            if (
                isinstance(category_count, int)
                and isinstance(categories_crawled, int)
                and categories_crawled < category_count
            ):
                raise RuntimeError(
                    "full crawl category reconciliation failed: "
                    f"{categories_crawled}/{category_count} in {path}"
                )
            products_listed = summary.get("productsListed")
            details_fetched = summary.get("productDetailsFetched")
            details_skipped = summary.get("productDetailsSkipped")
            if (
                isinstance(products_listed, int)
                and isinstance(details_fetched, int)
                and isinstance(details_skipped, int)
                and details_fetched + details_skipped != products_listed
            ):
                raise RuntimeError(
                    "full crawl detail reconciliation failed: "
                    f"listed={products_listed} fetched={details_fetched} "
                    f"skipped={details_skipped} in {path}"
                )
            product_file_count = summary.get("productFiles")
            if isinstance(product_file_count, int) and product_file_count != len(product_files):
                raise RuntimeError(
                    "full crawl product reconciliation failed: "
                    f"summary={product_file_count} files={len(product_files)} in {path}"
                )
            total_from_listing = summary.get("totalFromListing")
            product_refs = summary.get("productRefs")
            if (
                isinstance(total_from_listing, int)
                and isinstance(product_refs, list)
                and len(product_refs) < total_from_listing
            ):
                raise RuntimeError(
                    "full crawl listing reconciliation failed: "
                    f"refs={len(product_refs)} total={total_from_listing} in {path}"
                )
    return len(product_files)


def crawl_process_error(command: list[str], completed: Any) -> dict[str, Any]:
    """Build the bounded subprocess detail persisted with a failed run."""
    return {
        "returncode": completed.returncode,
        "command": command,
        "stdout_tail": str(completed.stdout or "")[-8000:],
        "stderr_tail": str(completed.stderr or "")[-8000:],
    }


def _operation_failure(
    connection: Any,
    run_id: str,
    stage: str,
    exc: Exception,
    *,
    output_path: Path,
) -> dict[str, Any]:
    """Persist an operational failure without discarding resumable files."""
    connection.rollback()
    error = {
        "stage": stage,
        "type": type(exc).__name__,
        "message": str(exc),
    }
    set_run_state(
        connection,
        run_id,
        "failed",
        error=error,
        output_path=str(output_path),
    )
    connection.commit()
    return {"run_id": run_id, "state": "failed", "error": error}


def _import_finalized_run(
    connection: Any,
    run_id: str,
    slug: str,
    final: Path,
) -> dict[str, Any]:
    try:
        counters = import_directory(connection, run_id, slug, final)
    except Exception as exc:
        return _operation_failure(
            connection,
            run_id,
            "import",
            exc,
            output_path=final,
        )
    return {
        "run_id": run_id,
        "state": "imported" if counters["errors"] == 0 else "succeeded",
        "output_path": str(final),
        "counters": counters,
    }


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
        recovered = recoverable_final_directory(connection, root, slug)
        if recovered is not None:
            run_id, final = recovered
            set_run_state(
                connection,
                run_id,
                "running",
                output_path=str(final),
            )
            connection.commit()
            return _import_finalized_run(connection, run_id, slug, final)

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
        completed = subprocess.run(
            command,
            cwd=app_root,
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.stdout:
            print(completed.stdout, end="")
        if completed.stderr:
            print(completed.stderr, end="", file=sys.stderr)
        if completed.returncode != 0:
            error = crawl_process_error(command, completed)
            set_run_state(connection, run_id, "failed", error=error)
            connection.commit()
            return {"run_id": run_id, "state": "failed", "error": error}

        try:
            product_file_count = validate_crawl_output(partial, mode)
        except Exception as exc:
            return _operation_failure(
                connection,
                run_id,
                "validate",
                exc,
                output_path=partial,
            )

        run_manifest = {
            "run_id": run_id,
            "marketplace": slug,
            "mode": mode,
            "host": socket.gethostname(),
            "command": command,
            "started_at": started_at.isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "product_file_count": product_file_count,
        }
        (partial / "run-manifest.json").write_text(
            json.dumps(run_manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        try:
            final = finalize_partial(root, slug, partial)
        except Exception as exc:
            return _operation_failure(
                connection,
                run_id,
                "finalize",
                exc,
                output_path=partial,
            )
        set_run_state(
            connection,
            run_id,
            "succeeded",
            output_path=str(final),
        )
        connection.commit()
        return _import_finalized_run(connection, run_id, slug, final)
