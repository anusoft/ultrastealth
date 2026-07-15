"""Create and apply verified PostgreSQL backfill exports."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .migrations import MIGRATIONS_ROOT, migration_checksum, migration_files


INCREMENTAL_QUERIES = {
    "crawl_runs": """
        SELECT row_to_json(record)::text
        FROM (
            SELECT DISTINCT r.*
            FROM shopping.crawl_runs r
            JOIN shopping.crawl_documents d ON d.run_id = r.id
            WHERE d.id > %s AND d.id <= %s
        ) record
        ORDER BY (record).created_at, (record).id
    """,
    "document_blobs": """
        SELECT row_to_json(record)::text
        FROM (
            SELECT DISTINCT b.*
            FROM shopping.document_blobs b
            JOIN shopping.crawl_documents d
              ON d.content_sha256 = b.content_sha256
            WHERE d.id > %s AND d.id <= %s
        ) record
        ORDER BY (record).content_sha256
    """,
    "crawl_documents": """
        SELECT row_to_json(record)::text
        FROM (
            SELECT * FROM shopping.crawl_documents
            WHERE id > %s AND id <= %s
        ) record
        ORDER BY (record).id
    """,
    "products_current": """
        SELECT row_to_json(record)::text
        FROM (
            SELECT * FROM shopping.products_current
            WHERE latest_document_id > %s AND latest_document_id <= %s
        ) record
        ORDER BY (record).marketplace_id, (record).source_product_id
    """,
    "product_revisions": """
        SELECT row_to_json(record)::text
        FROM (
            SELECT * FROM shopping.product_revisions
            WHERE id > %s AND id <= %s
        ) record
        ORDER BY (record).id
    """,
    "crawl_errors": """
        SELECT row_to_json(record)::text
        FROM (
            SELECT DISTINCT e.*
            FROM shopping.crawl_errors e
            JOIN shopping.crawl_documents d ON d.run_id = e.run_id
            WHERE d.id > %s AND d.id <= %s
        ) record
        ORDER BY (record).id
    """,
}


def sha256_file(path: Path) -> str:
    """Return the SHA-256 of a file without reading it all into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def export_manifest(
    batch_id: str,
    lower_document_id: int,
    upper_document_id: int,
    lower_revision_id: int,
    upper_revision_id: int,
    counts: dict[str, int],
) -> dict[str, Any]:
    """Build the stable metadata shared by incremental bundles."""
    return {
        "batch_id": batch_id,
        "type": "incremental",
        "lower_document_id": lower_document_id,
        "upper_document_id": upper_document_id,
        "lower_revision_id": lower_revision_id,
        "upper_revision_id": upper_revision_id,
        "counts": counts,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def bundle_import_state(batch_id: str, imported_ids: set[str]) -> str:
    """Return whether an incremental batch still needs application."""
    return "already_imported" if batch_id in imported_ids else "pending"


def verify_bundle_files(root: Path, manifest: dict[str, Any]) -> None:
    """Verify every stream byte count and SHA-256 in a bundle manifest."""
    for name, expected in manifest.get("files", {}).items():
        path = root / name
        if not path.is_file():
            raise RuntimeError(f"bundle file missing: {name}")
        if path.stat().st_size != expected["bytes"]:
            raise RuntimeError(f"byte count mismatch: {name}")
        actual = sha256_file(path)
        if actual != expected["sha256"]:
            raise RuntimeError(f"checksum mismatch: {name}")


def _schema_version(connection: Any) -> str:
    row = connection.execute(
        "SELECT version FROM shopping.schema_migrations ORDER BY version DESC LIMIT 1"
    ).fetchone()
    return str(row[0]) if row else "none"


def _write_stream(
    connection: Any,
    query: str,
    params: tuple[int, int],
    path: Path,
) -> int:
    count = 0
    cursor = connection.cursor(name=f"shopping_export_{uuid.uuid4().hex}")
    try:
        cursor.execute(query, params)
        with path.open("w", encoding="utf-8") as output:
            while rows := cursor.fetchmany(1000):
                for row in rows:
                    output.write(str(row[0]))
                    output.write("\n")
                    count += 1
    finally:
        cursor.close()
    return count


def _compress(path: Path) -> Path:
    compressed = path.with_suffix(path.suffix + ".zst")
    subprocess.run(
        ["zstd", "-q", "-f", "--rm", str(path), "-o", str(compressed)],
        check=True,
    )
    return compressed


def create_baseline_export(
    connection: Any,
    output_root: Path,
    database_url: str,
) -> Path:
    """Create a PostgreSQL custom-format baseline with a verified manifest."""
    batch_id = str(uuid.uuid4())
    root = output_root / "baseline" / batch_id
    root.mkdir(parents=True, exist_ok=False)
    dump_path = root / "shopping.dump"
    subprocess.run(
        [
            "pg_dump",
            f"--dbname={database_url}",
            "--format=custom",
            "--no-owner",
            "--file",
            str(dump_path),
        ],
        check=True,
    )
    checksum = sha256_file(dump_path)
    counts = {
        table: int(connection.execute(f"SELECT count(*) FROM shopping.{table}").fetchone()[0])
        for table in (
            "marketplaces",
            "crawl_runs",
            "crawl_errors",
            "document_blobs",
            "crawl_documents",
            "products_current",
            "product_revisions",
        )
    }
    manifest = {
        "batch_id": batch_id,
        "type": "baseline",
        "schema_version": _schema_version(connection),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "counts": counts,
        "files": {
            dump_path.name: {
                "bytes": dump_path.stat().st_size,
                "sha256": checksum,
            }
        },
    }
    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    connection.execute(
        """
        INSERT INTO shopping.export_batches (
            id, batch_type, schema_version, output_path, content_sha256,
            counts, state, completed_at
        )
        VALUES (%s, 'baseline', %s, %s, %s, %s::jsonb, 'complete', now())
        """,
        (
            batch_id,
            manifest["schema_version"],
            str(root),
            checksum,
            json.dumps(counts),
        ),
    )
    connection.commit()
    return root


def create_incremental_export(connection: Any, output_root: Path) -> Path:
    """Create a compressed, watermark-bounded incremental bundle."""
    last = connection.execute(
        """
        SELECT COALESCE(max(upper_document_id), 0),
               COALESCE(max(upper_revision_id), 0)
        FROM shopping.export_batches
        WHERE batch_type = 'incremental' AND state = 'complete'
        """
    ).fetchone()
    lower_document_id, lower_revision_id = int(last[0]), int(last[1])
    upper_document_id = int(
        connection.execute(
            "SELECT COALESCE(max(id), 0) FROM shopping.crawl_documents"
        ).fetchone()[0]
    )
    upper_revision_id = int(
        connection.execute(
            "SELECT COALESCE(max(id), 0) FROM shopping.product_revisions"
        ).fetchone()[0]
    )
    batch_id = str(uuid.uuid4())
    root = output_root / "incremental" / batch_id
    root.mkdir(parents=True, exist_ok=False)
    connection.execute(
        """
        INSERT INTO shopping.export_batches (
            id, batch_type, lower_document_id, upper_document_id,
            lower_revision_id, upper_revision_id, schema_version,
            output_path, state
        )
        VALUES (%s, 'incremental', %s, %s, %s, %s, %s, %s, 'running')
        """,
        (
            batch_id,
            lower_document_id,
            upper_document_id,
            lower_revision_id,
            upper_revision_id,
            _schema_version(connection),
            str(root),
        ),
    )
    connection.commit()

    counts: dict[str, int] = {}
    files: dict[str, dict[str, Any]] = {}
    for name, query in INCREMENTAL_QUERIES.items():
        raw_path = root / f"{name}.jsonl"
        bounds = (
            (lower_revision_id, upper_revision_id)
            if name == "product_revisions"
            else (lower_document_id, upper_document_id)
        )
        counts[name] = _write_stream(connection, query, bounds, raw_path)
        compressed = _compress(raw_path)
        files[compressed.name] = {
            "bytes": compressed.stat().st_size,
            "sha256": sha256_file(compressed),
        }

    migrations_dir = root / "migrations"
    migrations_dir.mkdir()
    migration_hashes = {}
    for source in migration_files(MIGRATIONS_ROOT):
        destination = migrations_dir / source.name
        shutil.copy2(source, destination)
        migration_hashes[source.name] = migration_checksum(source)

    manifest = export_manifest(
        batch_id,
        lower_document_id,
        upper_document_id,
        lower_revision_id,
        upper_revision_id,
        counts,
    )
    manifest.update(
        {
            "schema_version": _schema_version(connection),
            "migration_sha256": migration_hashes,
            "files": files,
        }
    )
    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    manifest_sha = sha256_file(manifest_path)
    connection.execute(
        """
        UPDATE shopping.export_batches
        SET counts = %s::jsonb,
            content_sha256 = %s,
            state = 'complete',
            completed_at = now()
        WHERE id = %s
        """,
        (json.dumps(counts), manifest_sha, batch_id),
    )
    connection.commit()
    return root


def _decompressed_lines(path: Path) -> Iterable[str]:
    process = subprocess.Popen(
        ["zstd", "-q", "-d", "-c", str(path)],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    try:
        yield from process.stdout
    finally:
        process.stdout.close()
        if process.wait() != 0:
            raise RuntimeError(f"zstd failed for {path.name}")


def _insert_record(connection: Any, table: str, record: str) -> None:
    identities = {"crawl_errors", "crawl_documents", "product_revisions"}
    overriding = " OVERRIDING SYSTEM VALUE" if table in identities else ""
    conflict = "DO NOTHING"
    if table == "crawl_runs":
        conflict = """DO UPDATE SET
            state = EXCLUDED.state,
            output_path = EXCLUDED.output_path,
            checkpoint = EXCLUDED.checkpoint,
            counters = EXCLUDED.counters,
            error = EXCLUDED.error,
            started_at = EXCLUDED.started_at,
            finished_at = EXCLUDED.finished_at,
            imported_at = EXCLUDED.imported_at"""
    elif table == "products_current":
        conflict = """DO UPDATE SET
            latest_document_id = EXCLUDED.latest_document_id,
            latest_run_id = EXCLUDED.latest_run_id,
            canonical_sha256 = EXCLUDED.canonical_sha256,
            sku = EXCLUDED.sku,
            title = EXCLUDED.title,
            brand = EXCLUDED.brand,
            source_url = EXCLUDED.source_url,
            current_price = EXCLUDED.current_price,
            regular_price = EXCLUDED.regular_price,
            currency = EXCLUDED.currency,
            availability = EXCLUDED.availability,
            category_path = EXCLUDED.category_path,
            image_urls = EXCLUDED.image_urls,
            rating = EXCLUDED.rating,
            review_count = EXCLUDED.review_count,
            projection = EXCLUDED.projection,
            last_seen_at = EXCLUDED.last_seen_at"""
    connection.execute(
        f"""
        INSERT INTO shopping.{table}{overriding}
        SELECT (jsonb_populate_record(NULL::shopping.{table}, %s::jsonb)).*
        ON CONFLICT {conflict}
        """,
        (record,),
    )


def import_incremental_bundle(connection: Any, root: Path) -> str:
    """Verify and idempotently apply an incremental bundle."""
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    verify_bundle_files(root, manifest)
    batch_id = manifest["batch_id"]
    imported = {
        str(row[0])
        for row in connection.execute(
            "SELECT id FROM shopping.imported_batches WHERE id = %s",
            (batch_id,),
        ).fetchall()
    }
    state = bundle_import_state(batch_id, imported)
    if state == "already_imported":
        return state

    for table in (
        "crawl_runs",
        "document_blobs",
        "crawl_documents",
        "products_current",
        "product_revisions",
        "crawl_errors",
    ):
        path = root / f"{table}.jsonl.zst"
        for line in _decompressed_lines(path):
            if line.strip():
                _insert_record(connection, table, line)
    for table in ("crawl_errors", "crawl_documents", "product_revisions"):
        connection.execute(
            f"""
            SELECT setval(
                pg_get_serial_sequence('shopping.{table}', 'id'),
                COALESCE((SELECT max(id) FROM shopping.{table}), 1),
                (SELECT count(*) > 0 FROM shopping.{table})
            )
            """
        )
    connection.execute(
        """
        INSERT INTO shopping.imported_batches (
            id, manifest_sha256, source_schema_version
        )
        VALUES (%s, %s, %s)
        """,
        (batch_id, sha256_file(manifest_path), manifest["schema_version"]),
    )
    connection.commit()
    return "imported"
