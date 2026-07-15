import json
import unittest
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from shopping_app.database import create_run, import_directory, import_document


class FakeResult:
    def __init__(self, row=None, rows=None):
        self.row = row
        self.rows = rows or []

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self):
        self.calls = []
        self.transaction_count = 0

    @contextmanager
    def transaction(self):
        self.transaction_count += 1
        yield

    def execute(self, sql, params=None):
        normalized = " ".join(sql.split())
        self.calls.append((normalized, params))
        if "SELECT id FROM shopping.marketplaces" in normalized:
            return FakeResult((7,))
        if "INSERT INTO shopping.crawl_documents" in normalized:
            return FakeResult((101,))
        return FakeResult()


class DatabaseTests(unittest.TestCase):
    def test_resumed_run_refreshes_mode_command_and_output_metadata(self):
        connection = FakeConnection()

        create_run(
            connection,
            "bigc",
            "full",
            "/shopping/partial/bigc/current",
            command_args=["bun", "bigc.mjs", "--resume"],
            run_id="00000000-0000-0000-0000-000000000001",
        )

        statement = next(
            sql for sql, _ in connection.calls if "INSERT INTO shopping.crawl_runs" in sql
        )
        self.assertIn("ON CONFLICT (id) DO UPDATE", statement)
        self.assertIn("mode = EXCLUDED.mode", statement)
        self.assertIn("command_args = EXCLUDED.command_args", statement)
        self.assertIn("output_path = EXCLUDED.output_path", statement)

    def test_product_import_stores_blob_document_projection_and_revision(self):
        connection = FakeConnection()
        content = json.dumps(
            {
                "id": "item-1",
                "name": "Item",
                "pricing": {"currency": "THB", "current": 10},
            }
        ).encode()

        result = import_document(
            connection,
            "00000000-0000-0000-0000-000000000001",
            "advice",
            "products/item-1.json",
            content,
        )

        statements = "\n".join(sql for sql, _ in connection.calls)
        self.assertIn("INSERT INTO shopping.document_blobs", statements)
        self.assertIn("INSERT INTO shopping.crawl_documents", statements)
        self.assertIn("INSERT INTO shopping.products_current", statements)
        self.assertIn("INSERT INTO shopping.product_revisions", statements)
        self.assertIn(
            "products_current.latest_document_id <= EXCLUDED.latest_document_id",
            statements,
        )
        self.assertEqual(result["document_id"], 101)
        self.assertTrue(result["projected"])

    def test_non_product_document_is_stored_without_projection(self):
        connection = FakeConnection()

        result = import_document(
            connection,
            "00000000-0000-0000-0000-000000000001",
            "advice",
            "summary.json",
            b'{"productsWritten":1}',
        )

        statements = "\n".join(sql for sql, _ in connection.calls)
        self.assertNotIn("INSERT INTO shopping.products_current", statements)
        self.assertFalse(result["projected"])
        self.assertEqual(result["kind"], "summary")

    def test_missing_product_identity_records_projection_error(self):
        connection = FakeConnection()

        result = import_document(
            connection,
            "00000000-0000-0000-0000-000000000001",
            "advice",
            "products/unknown.json",
            b'{"name":"Unknown"}',
        )

        statements = "\n".join(sql for sql, _ in connection.calls)
        self.assertIn("INSERT INTO shopping.crawl_errors", statements)
        self.assertIn(
            "ON CONFLICT (run_id, stage, relative_path, source_key) DO UPDATE",
            statements,
        )
        self.assertFalse(result["projected"])
        self.assertEqual(result["error"], "missing_product_identity")

    def test_non_object_product_json_is_retained_as_projection_error(self):
        connection = FakeConnection()

        result = import_document(
            connection,
            "00000000-0000-0000-0000-000000000001",
            "advice",
            "products/array.json",
            b"[]",
        )

        statements = "\n".join(sql for sql, _ in connection.calls)
        self.assertIn("INSERT INTO shopping.document_blobs", statements)
        self.assertIn("INSERT INTO shopping.crawl_documents", statements)
        self.assertIn("INSERT INTO shopping.crawl_errors", statements)
        self.assertEqual(result["error"], "missing_product_identity")

    def test_directory_import_walks_json_in_stable_order(self):
        connection = FakeConnection()
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "products").mkdir()
            (root / "products" / "b.json").write_text('{"id":"b"}')
            (root / "products" / "a.json").write_text('{"id":"a"}')
            (root / "readme.txt").write_text("not JSON")

            counters = import_directory(
                connection,
                "00000000-0000-0000-0000-000000000001",
                "advice",
                root,
            )

        self.assertEqual(counters["documents"], 2)
        self.assertEqual(counters["products"], 2)
        document_calls = [
            params
            for sql, params in connection.calls
            if "INSERT INTO shopping.crawl_documents" in sql
        ]
        self.assertEqual(
            [params[3] for params in document_calls],
            ["products/a.json", "products/b.json"],
        )
        self.assertEqual(connection.transaction_count, 3)

        document_statement = next(
            sql for sql, _ in connection.calls if "INSERT INTO shopping.crawl_documents" in sql
        )
        self.assertNotIn("captured_at = now()", document_statement)


if __name__ == "__main__":
    unittest.main()
