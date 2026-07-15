"""PostgreSQL persistence for marketplace crawl runs and documents."""

from __future__ import annotations

import json
import os
import socket
import uuid
from pathlib import Path
from typing import Any

from .documents import classify_path, product_projection, raw_digest


DEFAULT_DATABASE_URL = "dbname=shopping host=/var/run/postgresql"


def connect(database_url: str | None = None):
    """Open a PostgreSQL connection, importing psycopg only when needed."""
    import psycopg

    return psycopg.connect(
        database_url
        or os.environ.get("SHOPPING_DATABASE_URL", DEFAULT_DATABASE_URL),
        autocommit=False,
    )


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def marketplace_id(connection: Any, slug: str) -> int:
    row = connection.execute(
        "SELECT id FROM shopping.marketplaces WHERE slug = %s",
        (slug,),
    ).fetchone()
    if row is None:
        raise ValueError(f"unknown marketplace: {slug}")
    return int(row[0])


def create_run(
    connection: Any,
    slug: str,
    mode: str,
    output_path: str,
    command_args: list[str] | None = None,
    run_id: str | None = None,
    state: str = "queued",
) -> str:
    """Create an idempotent crawl run and return its UUID string."""
    identifier = run_id or str(uuid.uuid4())
    source_id = marketplace_id(connection, slug)
    connection.execute(
        """
        INSERT INTO shopping.crawl_runs (
            id, marketplace_id, mode, state, host, command_args, output_path
        )
        VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s)
        ON CONFLICT (id) DO UPDATE
        SET mode = EXCLUDED.mode,
            state = EXCLUDED.state,
            host = EXCLUDED.host,
            command_args = EXCLUDED.command_args,
            output_path = EXCLUDED.output_path
        """,
        (
            identifier,
            source_id,
            mode,
            state,
            socket.gethostname(),
            _json(command_args or []),
            output_path,
        ),
    )
    return identifier


def set_run_state(
    connection: Any,
    run_id: str,
    state: str,
    *,
    counters: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
    output_path: str | None = None,
) -> None:
    """Update a run state and its state-specific timestamp."""
    if state not in {"queued", "running", "succeeded", "failed", "imported"}:
        raise ValueError(f"invalid run state: {state}")
    connection.execute(
        """
        UPDATE shopping.crawl_runs
        SET state = %s,
            counters = COALESCE(%s::jsonb, counters),
            error = %s::jsonb,
            output_path = COALESCE(%s, output_path),
            started_at = CASE WHEN %s = 'running' THEN COALESCE(started_at, now()) ELSE started_at END,
            finished_at = CASE WHEN %s IN ('succeeded', 'failed', 'imported') THEN now() ELSE finished_at END,
            imported_at = CASE WHEN %s = 'imported' THEN now() ELSE imported_at END
        WHERE id = %s
        """,
        (
            state,
            _json(counters) if counters is not None else None,
            _json(error) if error is not None else None,
            output_path,
            state,
            state,
            state,
            run_id,
        ),
    )


def record_error(
    connection: Any,
    run_id: str,
    source_id: int,
    stage: str,
    detail: dict[str, Any],
    *,
    relative_path: str | None = None,
    source_key: str | None = None,
    retryable: bool = False,
) -> None:
    """Store a structured run error."""
    connection.execute(
        """
        INSERT INTO shopping.crawl_errors (
            run_id, marketplace_id, stage, source_key, relative_path,
            retryable, detail
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
        ON CONFLICT (run_id, stage, relative_path, source_key) DO UPDATE
        SET marketplace_id = EXCLUDED.marketplace_id,
            retryable = EXCLUDED.retryable,
            detail = EXCLUDED.detail
        """,
        (
            run_id,
            source_id,
            stage,
            source_key,
            relative_path,
            retryable,
            _json(detail),
        ),
    )


def import_document(
    connection: Any,
    run_id: str,
    slug: str,
    relative_path: str,
    content: bytes,
) -> dict[str, Any]:
    """Store one exact JSON document and update product projections."""
    source_id = marketplace_id(connection, slug)
    kind = classify_path(relative_path)
    digest = raw_digest(content)
    try:
        payload = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        record_error(
            connection,
            run_id,
            source_id,
            "json_parse",
            {"error": str(exc)},
            relative_path=relative_path,
        )
        return {"kind": kind, "projected": False, "error": "invalid_json"}

    projection: dict[str, Any] | None = None
    projection_error: str | None = None
    source_key = Path(relative_path).stem
    if kind == "product":
        try:
            if not isinstance(payload, dict):
                raise ValueError("product payload must be a JSON object")
            projection = product_projection(payload)
            source_key = projection["source_product_id"]
        except ValueError as exc:
            projection_error = str(exc)

    connection.execute(
        """
        INSERT INTO shopping.document_blobs (
            content_sha256, payload, byte_count
        )
        VALUES (%s, %s::jsonb, %s)
        ON CONFLICT (content_sha256) DO NOTHING
        """,
        (digest, _json(payload), len(content)),
    )
    document_id = connection.execute(
        """
        INSERT INTO shopping.crawl_documents (
            run_id, marketplace_id, document_kind, relative_path,
            source_key, content_sha256
        )
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (run_id, relative_path) DO UPDATE
        SET document_kind = EXCLUDED.document_kind,
            source_key = EXCLUDED.source_key,
            content_sha256 = EXCLUDED.content_sha256
        RETURNING id
        """,
        (run_id, source_id, kind, relative_path, source_key, digest),
    ).fetchone()[0]

    if projection_error is not None:
        record_error(
            connection,
            run_id,
            source_id,
            "product_projection",
            {"error": projection_error},
            relative_path=relative_path,
            source_key=source_key,
        )
        return {
            "document_id": int(document_id),
            "kind": kind,
            "projected": False,
            "error": "missing_product_identity",
        }

    if projection is None:
        return {"document_id": int(document_id), "kind": kind, "projected": False}

    connection.execute(
        """
        INSERT INTO shopping.products_current (
            marketplace_id, source_product_id, latest_document_id,
            latest_run_id, canonical_sha256, sku, title, brand, source_url,
            current_price, regular_price, currency, availability,
            category_path, image_urls, rating, review_count, projection
        )
        VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s, %s, %s::jsonb
        )
        ON CONFLICT (marketplace_id, source_product_id) DO UPDATE
        SET latest_document_id = EXCLUDED.latest_document_id,
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
            last_seen_at = now()
        WHERE shopping.products_current.latest_document_id
              <= EXCLUDED.latest_document_id
        """,
        (
            source_id,
            projection["source_product_id"],
            document_id,
            run_id,
            projection["canonical_sha256"],
            projection["sku"],
            projection["title"],
            projection["brand"],
            projection["source_url"],
            projection["current_price"],
            projection["regular_price"],
            projection["currency"],
            _json(projection["availability"]),
            _json(projection["category_path"]),
            _json(projection["image_urls"]),
            projection["rating"],
            projection["review_count"],
            _json(projection),
        ),
    )
    connection.execute(
        """
        INSERT INTO shopping.product_revisions (
            marketplace_id, source_product_id, run_id, document_id,
            canonical_sha256
        )
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (marketplace_id, source_product_id, canonical_sha256)
        DO NOTHING
        """,
        (
            source_id,
            projection["source_product_id"],
            run_id,
            document_id,
            projection["canonical_sha256"],
        ),
    )
    return {"document_id": int(document_id), "kind": kind, "projected": True}


def import_directory(
    connection: Any,
    run_id: str,
    slug: str,
    root: Path,
) -> dict[str, int]:
    """Import every JSON file below a run directory in stable order."""
    counters = {"documents": 0, "products": 0, "errors": 0}
    for path in sorted(root.rglob("*.json")):
        with connection.transaction():
            result = import_document(
                connection,
                run_id,
                slug,
                path.relative_to(root).as_posix(),
                path.read_bytes(),
            )
        counters["documents"] += 1
        if result.get("projected"):
            counters["products"] += 1
        if result.get("error"):
            counters["errors"] += 1
    with connection.transaction():
        set_run_state(
            connection,
            run_id,
            "imported" if counters["errors"] == 0 else "succeeded",
            counters=counters,
            output_path=str(root),
        )
    return counters


def select_due_marketplace(connection: Any) -> str | None:
    """Claim the next due marketplace for a scheduler transaction."""
    row = connection.execute(
        """
        SELECT slug
        FROM shopping.marketplaces
        WHERE enabled AND next_crawl_at <= now()
        ORDER BY priority, next_crawl_at, slug
        FOR UPDATE SKIP LOCKED
        LIMIT 1
        """
    ).fetchone()
    return str(row[0]) if row else None


def schedule_success(connection: Any, slug: str) -> None:
    connection.execute(
        """
        UPDATE shopping.marketplaces
        SET next_crawl_at = now() + make_interval(secs => crawl_interval_seconds),
            retry_count = 0,
            updated_at = now()
        WHERE slug = %s
        """,
        (slug,),
    )


def schedule_failure(connection: Any, slug: str) -> None:
    connection.execute(
        """
        UPDATE shopping.marketplaces
        SET retry_count = LEAST(retry_count + 1, 8),
            next_crawl_at = now() + make_interval(
                secs => LEAST(21600, 300 * power(2, LEAST(retry_count, 6)))::integer
            ),
            updated_at = now()
        WHERE slug = %s
        """,
        (slug,),
    )


def health_snapshot(connection: Any) -> dict[str, Any]:
    """Return counts used by the operational health command."""
    due_marketplaces = [
        str(row[0])
        for row in connection.execute(
            """
            SELECT slug FROM shopping.marketplaces
            WHERE enabled AND next_crawl_at <= now()
            ORDER BY priority, next_crawl_at, slug
            """
        ).fetchall()
    ]
    last_success = connection.execute(
        """
        SELECT m.slug, r.id, r.mode, r.imported_at
        FROM shopping.crawl_runs r
        JOIN shopping.marketplaces m ON m.id = r.marketplace_id
        WHERE r.state = 'imported'
        ORDER BY r.imported_at DESC NULLS LAST
        LIMIT 1
        """
    ).fetchone()
    last_failure = connection.execute(
        """
        SELECT m.slug, r.id, r.mode, r.finished_at, r.error
        FROM shopping.crawl_runs r
        JOIN shopping.marketplaces m ON m.id = r.marketplace_id
        WHERE r.state = 'failed'
        ORDER BY r.finished_at DESC NULLS LAST
        LIMIT 1
        """
    ).fetchone()
    export_watermarks = connection.execute(
        """
        SELECT COALESCE(max(upper_document_id), 0),
               COALESCE(max(upper_revision_id), 0)
        FROM shopping.export_batches
        WHERE batch_type = 'incremental' AND state = 'complete'
        """
    ).fetchone()
    snapshot = {
        "marketplaces": connection.execute(
            "SELECT count(*) FROM shopping.marketplaces"
        ).fetchone()[0],
        "due": len(due_marketplaces),
        "due_marketplaces": due_marketplaces,
        "running": connection.execute(
            "SELECT count(*) FROM shopping.crawl_runs WHERE state = 'running'"
        ).fetchone()[0],
        "documents": connection.execute(
            "SELECT count(*) FROM shopping.crawl_documents"
        ).fetchone()[0],
        "products": connection.execute(
            "SELECT count(*) FROM shopping.products_current"
        ).fetchone()[0],
        "errors": connection.execute(
            "SELECT count(*) FROM shopping.crawl_errors"
        ).fetchone()[0],
        "last_success": (
            {
                "site": last_success[0],
                "run_id": last_success[1],
                "mode": last_success[2],
                "at": last_success[3],
            }
            if last_success
            else None
        ),
        "last_failure": (
            {
                "site": last_failure[0],
                "run_id": last_failure[1],
                "mode": last_failure[2],
                "at": last_failure[3],
                "error": last_failure[4],
            }
            if last_failure
            else None
        ),
        "export_watermarks": {
            "document_id": int(export_watermarks[0]),
            "revision_id": int(export_watermarks[1]),
        },
    }
    return snapshot
