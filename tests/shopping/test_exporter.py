import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from shopping_app.exporter import (
    INCREMENTAL_QUERIES,
    _insert_record,
    bundle_import_state,
    baseline_dump_command,
    completed_export_watermarks,
    current_source_watermarks,
    export_manifest,
    sha256_file,
    validate_watermark_chain,
    verify_bundle_files,
    verify_incremental_stream_set,
    verify_bundle_migrations,
)


class RecordingConnection:
    def __init__(self):
        self.statements = []

    def execute(self, sql, params=None):
        self.statements.append(" ".join(sql.split()))


class WatermarkConnection:
    def __init__(self, row):
        self.row = row
        self.sql = ""

    def execute(self, sql, params=None):
        self.sql = " ".join(sql.split())
        return self

    def fetchone(self):
        return self.row


class ExporterTests(unittest.TestCase):
    def test_mutable_records_upsert_on_their_primary_keys(self):
        connection = RecordingConnection()

        _insert_record(connection, "crawl_runs", "{}")
        _insert_record(connection, "products_current", "{}")

        self.assertIn("ON CONFLICT (id) DO UPDATE", connection.statements[0])
        self.assertIn(
            "ON CONFLICT (marketplace_id, source_product_id) DO UPDATE",
            connection.statements[1],
        )
        self.assertIn(
            "products_current.latest_document_id <= EXCLUDED.latest_document_id",
            connection.statements[1],
        )

    def test_incremental_run_and_error_streams_do_not_depend_on_documents(self):
        self.assertNotIn("JOIN shopping.crawl_documents", INCREMENTAL_QUERIES["crawl_runs"])
        self.assertNotIn("JOIN shopping.crawl_documents", INCREMENTAL_QUERIES["crawl_errors"])

    def test_bundle_migration_checksums_are_verified(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            migrations = root / "migrations"
            migrations.mkdir()
            migration = migrations / "001_initial.sql"
            migration.write_text("SELECT 1;", encoding="utf-8")
            manifest = {
                "migration_sha256": {
                    migration.name: sha256_file(migration),
                }
            }

            verify_bundle_migrations(root, manifest)
            migration.write_text("SELECT 2;", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "migration checksum mismatch"):
                verify_bundle_migrations(root, manifest)

    def test_bundle_migration_name_cannot_escape_bundle(self):
        with TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, "unsafe migration path"):
                verify_bundle_migrations(
                    Path(directory),
                    {"migration_sha256": {"../001.sql": "0" * 64}},
                )

    def test_bundle_rejects_unlisted_migration_file(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            migrations = root / "migrations"
            migrations.mkdir()
            listed = migrations / "001_initial.sql"
            listed.write_text("SELECT 1;", encoding="utf-8")
            (migrations / "999_unlisted.sql").write_text(
                "SELECT dangerous();", encoding="utf-8"
            )

            with self.assertRaisesRegex(RuntimeError, "migration file set mismatch"):
                verify_bundle_migrations(
                    root,
                    {"migration_sha256": {listed.name: sha256_file(listed)}},
                )

    def test_completed_export_watermark_includes_baselines(self):
        connection = WatermarkConnection((700, 235))

        self.assertEqual(completed_export_watermarks(connection), (700, 235))
        self.assertNotIn("batch_type", connection.sql)

    def test_current_watermarks_share_one_database_snapshot(self):
        connection = WatermarkConnection((700, 235))

        self.assertEqual(current_source_watermarks(connection), (700, 235))
        self.assertIn("crawl_documents", connection.sql)
        self.assertIn("product_revisions", connection.sql)

    def test_baseline_dump_uses_exported_repeatable_read_snapshot(self):
        command = baseline_dump_command(
            "dbname=shopping",
            Path("/tmp/shopping.dump"),
            "00000003-0000001B-1",
        )

        self.assertIn("--snapshot=00000003-0000001B-1", command)

    def test_incremental_watermarks_must_continue_target_state(self):
        manifest = {"lower_document_id": 20, "lower_revision_id": 10}

        validate_watermark_chain(20, 10, manifest)

        with self.assertRaisesRegex(RuntimeError, "non-contiguous document watermark"):
            validate_watermark_chain(19, 10, manifest)

    def test_manifest_records_watermarks_and_counts(self):
        manifest = export_manifest(
            "00000000-0000-0000-0000-000000000001",
            10,
            20,
            30,
            40,
            {"crawl_documents": 11},
        )

        self.assertEqual(manifest["lower_document_id"], 10)
        self.assertEqual(manifest["upper_document_id"], 20)
        self.assertEqual(manifest["lower_revision_id"], 30)
        self.assertEqual(manifest["upper_revision_id"], 40)
        self.assertEqual(manifest["counts"]["crawl_documents"], 11)

    def test_sha256_file_hashes_exact_bytes(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "data.jsonl.zst"
            path.write_bytes(b"exact bytes")

            self.assertEqual(
                sha256_file(path),
                "e38e581aade78b64cc86f7ac9f3555ca78c2dcca747942a7f1d9b3275a834f75",
            )

    def test_repeated_bundle_is_reported_as_already_imported(self):
        self.assertEqual(
            bundle_import_state(
                "00000000-0000-0000-0000-000000000001",
                {"00000000-0000-0000-0000-000000000001"},
            ),
            "already_imported",
        )

    def test_bundle_verification_rejects_changed_stream(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            stream = root / "crawl_documents.jsonl.zst"
            stream.write_bytes(b"first")
            manifest = {
                "files": {
                    stream.name: {
                        "sha256": sha256_file(stream),
                        "bytes": stream.stat().st_size,
                    }
                }
            }
            stream.write_bytes(b"other")

            with self.assertRaisesRegex(RuntimeError, "checksum mismatch"):
                verify_bundle_files(root, manifest)

    def test_incremental_manifest_must_list_every_imported_stream(self):
        with self.assertRaisesRegex(RuntimeError, "incremental stream set mismatch"):
            verify_incremental_stream_set(
                {"files": {"crawl_runs.jsonl.zst": {}}}
            )


if __name__ == "__main__":
    unittest.main()
