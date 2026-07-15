import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from shopping_app.migrations import migration_checksum, migration_files


EXPECTED_TABLES = {
    "schema_migrations",
    "marketplaces",
    "crawl_runs",
    "crawl_errors",
    "document_blobs",
    "crawl_documents",
    "products_current",
    "product_revisions",
    "export_batches",
    "imported_batches",
}


class MigrationTests(unittest.TestCase):
    def test_migration_files_are_sorted_sql_files(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "002_second.sql").write_text("SELECT 2;")
            (root / "README.md").write_text("ignored")
            (root / "001_first.sql").write_text("SELECT 1;")

            self.assertEqual(
                [path.name for path in migration_files(root)],
                ["001_first.sql", "002_second.sql"],
            )

    def test_checksum_changes_with_migration_content(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "001.sql"
            path.write_text("SELECT 1;")
            first = migration_checksum(path)
            path.write_text("SELECT 2;")

            self.assertNotEqual(first, migration_checksum(path))

    def test_initial_schema_contains_every_required_table(self):
        sql = Path("shopping_app/db/migrations/001_initial.sql").read_text()
        tables = set(re.findall(r"CREATE TABLE shopping\.([a-z_]+)", sql))

        self.assertEqual(tables, EXPECTED_TABLES)

    def test_initial_schema_has_idempotency_constraints(self):
        sql = Path("shopping_app/db/migrations/001_initial.sql").read_text()

        self.assertIn("UNIQUE (run_id, relative_path)", sql)
        self.assertIn(
            "UNIQUE (marketplace_id, source_product_id, canonical_sha256)",
            sql,
        )
        self.assertIn("PRIMARY KEY (marketplace_id, source_product_id)", sql)

    def test_incremental_import_role_can_advance_identity_sequences(self):
        sql = Path(
            "shopping_app/db/migrations/002_incremental_import_sequences.sql"
        ).read_text()

        self.assertIn(
            "GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA shopping TO shopping",
            sql,
        )

    def test_crawl_error_identity_is_unique_with_nullable_keys(self):
        sql = Path(
            "shopping_app/db/migrations/003_idempotent_crawl_errors.sql"
        ).read_text()

        self.assertIn("CREATE UNIQUE INDEX crawl_errors_identity_idx", sql)
        self.assertIn("NULLS NOT DISTINCT", sql)

    def test_seed_contains_exact_marketplace_slugs(self):
        sql = Path("shopping_app/db/seeds/001_marketplaces.sql").read_text()
        slugs = set(re.findall(r"\('([a-z0-9]+)',", sql))

        from shopping_app.manifest import REQUIRED_SITES

        self.assertEqual(slugs, set(REQUIRED_SITES))
        self.assertIn("ON CONFLICT (slug) DO UPDATE", sql)
        conflict_update = sql.split("ON CONFLICT (slug) DO UPDATE", 1)[1]
        self.assertNotIn("crawl_interval_seconds =", conflict_update)
        self.assertNotIn("priority =", conflict_update)
        self.assertNotIn("enabled =", conflict_update)


if __name__ == "__main__":
    unittest.main()
