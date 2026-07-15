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

from .migrations import (
    MIGRATIONS_ROOT,
    apply_migrations,
    migration_checksum,
    migration_files,
)


INCREMENTAL_QUERIES = {
    "crawl_runs": """
        SELECT row_to_json(record)::text
        FROM (
            SELECT * FROM shopping.crawl_runs
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
            SELECT * FROM shopping.crawl_errors
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


def verify_incremental_stream_set(manifest: dict[str, Any]) -> None:
    """Require checksums for exactly the streams the importer will load."""
    expected = {f"{table}.jsonl.zst" for table in INCREMENTAL_QUERIES}
    actual = set(manifest.get("files", {}))
    if actual != expected:
        raise RuntimeError(
            "incremental stream set mismatch: "
            f"expected={sorted(expected)} actual={sorted(actual)}"
        )


def verify_bundle_migrations(root: Path, manifest: dict[str, Any]) -> None:
    """Reject missing, altered, or path-escaping migration files."""
    expected = manifest.get("migration_sha256")
    if not isinstance(expected, dict) or not expected:
        raise RuntimeError("bundle has no migration checksums")
    migrations_root = root / "migrations"
    for name in expected:
        if Path(name).name != name or not name.endswith(".sql"):
            raise RuntimeError(f"unsafe migration path: {name}")
    actual_names = {path.name for path in migration_files(migrations_root)}
    expected_names = set(expected)
    if actual_names != expected_names:
        raise RuntimeError(
            "migration file set mismatch: "
            f"expected={sorted(expected_names)} actual={sorted(actual_names)}"
        )
    for name, checksum in expected.items():
        path = migrations_root / name
        if not path.is_file():
            raise RuntimeError(f"bundle migration missing: {name}")
        if migration_checksum(path) != checksum:
            raise RuntimeError(f"migration checksum mismatch: {name}")


def validate_watermark_chain(
    target_document_id: int,
    target_revision_id: int,
    manifest: dict[str, Any],
) -> None:
    """Require an incremental bundle to continue the target's source IDs."""
    lower_document_id = int(manifest["lower_document_id"])
    lower_revision_id = int(manifest["lower_revision_id"])
    if target_document_id != lower_document_id:
        raise RuntimeError(
            "non-contiguous document watermark: "
            f"target={target_document_id} bundle={lower_document_id}"
        )
    if target_revision_id != lower_revision_id:
        raise RuntimeError(
            "non-contiguous revision watermark: "
            f"target={target_revision_id} bundle={lower_revision_id}"
        )


def _ensure_bundle_migrations(
    connection: Any,
    root: Path,
    manifest: dict[str, Any],
) -> None:
    """Validate bundle history and apply only migrations absent on the target."""
    verify_bundle_migrations(root, manifest)
    exists = connection.execute(
        "SELECT to_regclass('shopping.schema_migrations') IS NOT NULL"
    ).fetchone()[0]
    applied = {}
    if exists:
        applied = dict(
            connection.execute(
                "SELECT version, checksum FROM shopping.schema_migrations"
            ).fetchall()
        )
    expected = manifest["migration_sha256"]
    for name, checksum in expected.items():
        version = Path(name).stem
        if version in applied and applied[version] != checksum:
            raise RuntimeError(f"applied migration changed: {name}")
    if any(Path(name).stem not in applied for name in expected):
        can_migrate = connection.execute(
            """
            SELECT current_user = 'shopping_owner'
                   OR pg_has_role(current_user, 'shopping_owner', 'MEMBER')
            """
        ).fetchone()[0]
        if not can_migrate:
            raise RuntimeError(
                "pending bundle migrations require a shopping_owner/admin connection"
            )
        apply_migrations(
            connection,
            migrations_root=root / "migrations",
            seeds_root=root / "_no_seeds",
        )


def _schema_version(connection: Any) -> str:
    row = connection.execute(
        "SELECT version FROM shopping.schema_migrations ORDER BY version DESC LIMIT 1"
    ).fetchone()
    return str(row[0]) if row else "none"


def current_source_watermarks(connection: Any) -> tuple[int, int]:
    """Return the source IDs captured by a baseline or incremental export."""
    row = connection.execute(
        """
        SELECT COALESCE((SELECT max(id) FROM shopping.crawl_documents), 0),
               COALESCE((SELECT max(id) FROM shopping.product_revisions), 0)
        """
    ).fetchone()
    return int(row[0]), int(row[1])


def completed_export_watermarks(connection: Any) -> tuple[int, int]:
    """Continue from the newest completed baseline or incremental snapshot."""
    row = connection.execute(
        """
        SELECT COALESCE(max(upper_document_id), 0),
               COALESCE(max(upper_revision_id), 0)
        FROM shopping.export_batches
        WHERE state = 'complete'
        """
    ).fetchone()
    return int(row[0]), int(row[1])


def _write_stream(
    connection: Any,
    query: str,
    params: tuple[int, int],
    path: Path,
) -> int:
    count = 0
    cursor = connection.cursor(name=f"shopping_export_{uuid.uuid4().hex}")
    try:
        cursor.execute(query, params if "%s" in query else None)
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


def baseline_dump_command(
    database_url: str,
    dump_path: Path,
    snapshot_id: str,
) -> list[str]:
    """Build the pg_dump command pinned to an exported database snapshot."""
    return [
        "pg_dump",
        f"--dbname={database_url}",
        "--format=custom",
        "--no-owner",
        f"--snapshot={snapshot_id}",
        "--file",
        str(dump_path),
    ]


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
    with connection.transaction():
        connection.execute(
            "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
        )
        snapshot_id = str(
            connection.execute("SELECT pg_export_snapshot()").fetchone()[0]
        )
        upper_document_id, upper_revision_id = current_source_watermarks(connection)
        subprocess.run(
            baseline_dump_command(database_url, dump_path, snapshot_id),
            check=True,
        )
        counts = {
            table: int(
                connection.execute(f"SELECT count(*) FROM shopping.{table}").fetchone()[0]
            )
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
        schema_version = _schema_version(connection)
    checksum = sha256_file(dump_path)
    manifest = {
        "batch_id": batch_id,
        "type": "baseline",
        "schema_version": schema_version,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "counts": counts,
        "upper_document_id": upper_document_id,
        "upper_revision_id": upper_revision_id,
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
            id, batch_type, upper_document_id, upper_revision_id,
            schema_version, output_path, content_sha256, counts, state,
            completed_at
        )
        VALUES (
            %s, 'baseline', %s, %s, %s, %s, %s, %s::jsonb,
            'complete', now()
        )
        """,
        (
            batch_id,
            upper_document_id,
            upper_revision_id,
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
    batch_id = str(uuid.uuid4())
    root = output_root / "incremental" / batch_id
    root.mkdir(parents=True, exist_ok=False)
    with connection.transaction():
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        lower_document_id, lower_revision_id = completed_export_watermarks(connection)
        upper_document_id, upper_revision_id = current_source_watermarks(connection)
        schema_version = _schema_version(connection)
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
                schema_version,
                str(root),
            ),
        )

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
                "schema_version": schema_version,
                "migration_sha256": migration_hashes,
                "files": files,
            }
        )
        manifest_path = root / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, indent=2) + "\n",
            encoding="utf-8",
        )
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
        conflict = """(id) DO UPDATE SET
            state = EXCLUDED.state,
            output_path = EXCLUDED.output_path,
            checkpoint = EXCLUDED.checkpoint,
            counters = EXCLUDED.counters,
            error = EXCLUDED.error,
            started_at = EXCLUDED.started_at,
            finished_at = EXCLUDED.finished_at,
            imported_at = EXCLUDED.imported_at"""
    elif table == "products_current":
        conflict = """(marketplace_id, source_product_id) DO UPDATE SET
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
            last_seen_at = EXCLUDED.last_seen_at
        WHERE shopping.products_current.latest_document_id
              <= EXCLUDED.latest_document_id"""
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
    verify_incremental_stream_set(manifest)
    verify_bundle_files(root, manifest)
    _ensure_bundle_migrations(connection, root, manifest)
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

    watermarks = connection.execute(
        """
        SELECT COALESCE((SELECT max(id) FROM shopping.crawl_documents), 0),
               COALESCE((SELECT max(id) FROM shopping.product_revisions), 0)
        """
    ).fetchone()
    validate_watermark_chain(int(watermarks[0]), int(watermarks[1]), manifest)

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
