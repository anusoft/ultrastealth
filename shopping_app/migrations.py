"""Apply ordered, checksummed PostgreSQL migrations and seeds."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any


DB_ROOT = Path(__file__).with_name("db")
MIGRATIONS_ROOT = DB_ROOT / "migrations"
SEEDS_ROOT = DB_ROOT / "seeds"


def migration_files(root: Path = MIGRATIONS_ROOT) -> list[Path]:
    """Return ordered SQL files in a migration or seed directory."""
    return sorted(path for path in root.glob("*.sql") if path.is_file())


def migration_checksum(path: Path) -> str:
    """Return the SHA-256 of a migration file."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _applied_migrations(connection: Any) -> dict[str, str]:
    exists = connection.execute(
        "SELECT to_regclass('shopping.schema_migrations') IS NOT NULL"
    ).fetchone()[0]
    if not exists:
        return {}
    rows = connection.execute(
        "SELECT version, checksum FROM shopping.schema_migrations"
    ).fetchall()
    return dict(rows)


def apply_migrations(
    connection: Any,
    migrations_root: Path = MIGRATIONS_ROOT,
    seeds_root: Path = SEEDS_ROOT,
) -> list[str]:
    """Apply missing migrations, reject changed history, then apply seeds."""
    connection.execute("SET ROLE shopping_owner")
    applied = _applied_migrations(connection)
    installed: list[str] = []
    for path in migration_files(migrations_root):
        version = path.stem
        checksum = migration_checksum(path)
        if version in applied:
            if applied[version] != checksum:
                raise RuntimeError(f"applied migration changed: {path.name}")
            continue
        with connection.transaction():
            connection.execute(path.read_text(encoding="utf-8"))
            connection.execute(
                """
                INSERT INTO shopping.schema_migrations (version, checksum)
                VALUES (%s, %s)
                """,
                (version, checksum),
            )
        installed.append(version)
    for path in migration_files(seeds_root):
        with connection.transaction():
            connection.execute(path.read_text(encoding="utf-8"))
    return installed
