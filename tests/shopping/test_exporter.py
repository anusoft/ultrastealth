import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from shopping_app.exporter import (
    bundle_import_state,
    export_manifest,
    sha256_file,
    verify_bundle_files,
)


class ExporterTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
