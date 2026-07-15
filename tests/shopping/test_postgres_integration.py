import os
import subprocess
import unittest
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory

from shopping_app.database import create_run, import_directory
from shopping_app.exporter import (
    create_baseline_export,
    create_incremental_export,
    import_incremental_bundle,
)
from shopping_app.migrations import apply_migrations


DATABASE_URL = os.environ.get("SHOPPING_TEST_DATABASE_URL")
ADMIN_DATABASE_URL = os.environ.get(
    "SHOPPING_TEST_ADMIN_DATABASE_URL",
    "dbname=postgres host=/var/run/postgresql",
)


@unittest.skipUnless(DATABASE_URL, "set SHOPPING_TEST_DATABASE_URL for PostgreSQL tests")
class PostgresIntegrationTests(unittest.TestCase):
    def test_migrate_ingest_baseline_delta_and_replay(self):
        import psycopg
        from psycopg import sql

        target_name = f"shopping_it_target_{uuid.uuid4().hex[:12]}"
        with psycopg.connect(ADMIN_DATABASE_URL, autocommit=True) as admin:
            admin.execute(
                sql.SQL("CREATE DATABASE {} OWNER shopping_owner").format(
                    sql.Identifier(target_name)
                )
            )
        try:
            with TemporaryDirectory() as directory:
                root = Path(directory)
                exports = root / "exports"
                first = root / "first"
                bad = root / "bad"
                delta = root / "delta"
                for path in (first / "products", bad / "products", delta / "products"):
                    path.mkdir(parents=True)
                (first / "products" / "one.json").write_text(
                    '{"id":"one","name":"Old","pricing":{"current":10}}'
                )
                (bad / "products" / "bad.json").write_text("not-json")
                (delta / "products" / "one.json").write_text(
                    '{"id":"one","name":"New","pricing":{"current":20}}'
                )

                with psycopg.connect(DATABASE_URL) as source:
                    self.assertTrue(apply_migrations(source))
                    source.commit()
                    self.assertEqual(apply_migrations(source), [])
                    source.commit()

                    first_run = create_run(source, "advice", "initial", str(first))
                    source.commit()
                    import_directory(source, first_run, "advice", first)

                    bad_run = create_run(source, "advice", "initial", str(bad))
                    source.commit()
                    import_directory(source, bad_run, "advice", bad)
                    import_directory(source, bad_run, "advice", bad)
                    self.assertEqual(
                        source.execute(
                            "SELECT count(*) FROM shopping.crawl_errors"
                        ).fetchone()[0],
                        1,
                    )
                    source.commit()

                    baseline = create_baseline_export(source, exports, DATABASE_URL)

                    delta_run = create_run(source, "advice", "full", str(delta))
                    source.commit()
                    import_directory(source, delta_run, "advice", delta)
                    incremental = create_incremental_export(source, exports)
                    source_counts = source.execute(
                        """
                        SELECT (SELECT count(*) FROM shopping.crawl_runs),
                               (SELECT count(*) FROM shopping.crawl_documents),
                               (SELECT count(*) FROM shopping.document_blobs),
                               (SELECT count(*) FROM shopping.products_current),
                               (SELECT count(*) FROM shopping.product_revisions),
                               (SELECT count(*) FROM shopping.crawl_errors)
                        """
                    ).fetchone()

                target_url = f"dbname={target_name} host=/var/run/postgresql"
                subprocess.run(
                    [
                        "pg_restore",
                        "--exit-on-error",
                        "--no-owner",
                        f"--dbname={target_url}",
                        str(baseline / "shopping.dump"),
                    ],
                    check=True,
                )
                with psycopg.connect(target_url) as target:
                    self.assertEqual(
                        import_incremental_bundle(target, incremental),
                        "imported",
                    )
                    self.assertEqual(
                        import_incremental_bundle(target, incremental),
                        "already_imported",
                    )
                    target_counts = target.execute(
                        """
                        SELECT (SELECT count(*) FROM shopping.crawl_runs),
                               (SELECT count(*) FROM shopping.crawl_documents),
                               (SELECT count(*) FROM shopping.document_blobs),
                               (SELECT count(*) FROM shopping.products_current),
                               (SELECT count(*) FROM shopping.product_revisions),
                               (SELECT count(*) FROM shopping.crawl_errors)
                        """
                    ).fetchone()
                    title = target.execute(
                        "SELECT title FROM shopping.products_current"
                    ).fetchone()[0]

                self.assertEqual(target_counts, source_counts)
                self.assertEqual(title, "New")
        finally:
            with psycopg.connect(ADMIN_DATABASE_URL, autocommit=True) as admin:
                admin.execute(
                    sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                        sql.Identifier(target_name)
                    )
                )


if __name__ == "__main__":
    unittest.main()
