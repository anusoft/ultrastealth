import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from shopping_app.runner import (
    build_crawl_command,
    crawl_process_error,
    ensure_disk_space,
    finalize_partial,
    partial_directory,
    recoverable_final_directory,
    run_id_for_partial,
    validate_crawl_output,
)


class FakeRows:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class RecoveryConnection:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, sql, params=None):
        return FakeRows(self.rows)


class RunnerTests(unittest.TestCase):
    def test_disk_guard_rejects_less_than_ten_gib(self):
        with self.assertRaisesRegex(RuntimeError, "requires 10 GiB free"):
            ensure_disk_space(10 * 1024**3 - 1)

    def test_disk_guard_accepts_exactly_ten_gib(self):
        ensure_disk_space(10 * 1024**3)

    def test_partial_directory_reuses_current_run(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)

            first = partial_directory(root, "advice")
            second = partial_directory(root, "advice")

            self.assertEqual(first, second)
            self.assertEqual(run_id_for_partial(first), run_id_for_partial(second))

    def test_command_uses_bun_explicit_output_and_manifest_args(self):
        site = {
            "entrypoint": "out/advice/advice.mjs",
            "smoke_args": ["--category-limit", "1", "--resume"],
        }

        command = build_crawl_command(
            site,
            "smoke",
            Path("/shopping/app"),
            Path("/shopping/partial/advice/current"),
            "/var/lib/shopping/.bun/bin/bun",
        )

        self.assertEqual(command[:3], [
            "/var/lib/shopping/.bun/bin/bun",
            "run",
            "/shopping/app/out/advice/advice.mjs",
        ])
        self.assertEqual(
            command[-2:],
            ["--out", "/shopping/partial/advice/current"],
        )

    def test_finalize_renames_current_directory_to_run_id(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            partial = partial_directory(root, "advice")
            (partial / "summary.json").write_text(json.dumps({"products": 1}))
            run_id = run_id_for_partial(partial)

            final = finalize_partial(root, "advice", partial)

            self.assertEqual(final, root / "data" / "advice" / run_id)
            self.assertTrue((final / "summary.json").exists())
            self.assertFalse(partial.exists())

    def test_recovery_finds_finalized_run_that_was_not_imported(self):
        run_id = "00000000-0000-0000-0000-000000000001"
        with TemporaryDirectory() as directory:
            root = Path(directory)
            final = root / "data" / "advice" / run_id
            final.mkdir(parents=True)

            recovered = recoverable_final_directory(
                RecoveryConnection([(run_id,)]),
                root,
                "advice",
            )

        self.assertEqual(recovered, (run_id, final))

    def test_crawl_output_requires_at_least_one_product_document(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "summary.json").write_text("{}")

            with self.assertRaisesRegex(RuntimeError, "no product JSON"):
                validate_crawl_output(root)

            (root / "products").mkdir()
            (root / "products" / "one.json").write_text('{"id":"one"}')
            self.assertEqual(validate_crawl_output(root), 1)

    def test_process_failure_persists_stderr_tail(self):
        detail = crawl_process_error(
            ["bun", "crawler.mjs"],
            SimpleNamespace(returncode=1, stdout="out", stderr="blocked by CDN"),
        )

        self.assertEqual(detail["returncode"], 1)
        self.assertEqual(detail["stderr_tail"], "blocked by CDN")

    def test_full_crawl_reconciles_summary_counts(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "products").mkdir()
            (root / "products" / "one.json").write_text('{"id":"one"}')
            (root / "summary.json").write_text(
                json.dumps(
                    {
                        "categoryCount": 10,
                        "categoriesCrawled": 1,
                        "productFiles": 2,
                    }
                )
            )

            with self.assertRaisesRegex(RuntimeError, "category reconciliation"):
                validate_crawl_output(root, "full")

            self.assertEqual(validate_crawl_output(root, "smoke"), 1)

    def test_full_crawl_reconciles_custom_summary_schemas(self):
        fixtures = (
            (
                {
                    "departmentsDiscovered": 2,
                    "departmentsSelected": 1,
                    "productsListed": 1,
                    "productDetailsFetched": 1,
                    "productDetailsSkipped": 0,
                },
                "category reconciliation",
            ),
            (
                {
                    "categoriesDiscovered": 1,
                    "categoriesSelected": 1,
                    "productsListed": 2,
                    "productDetailsFetched": 1,
                    "productDetailsSkipped": 0,
                },
                "detail reconciliation",
            ),
        )
        for summary, message in fixtures:
            with self.subTest(summary=summary), TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "products").mkdir()
                (root / "products" / "one.json").write_text('{"id":"one"}')
                (root / "summary.json").write_text(json.dumps(summary))

                with self.assertRaisesRegex(RuntimeError, message):
                    validate_crawl_output(root, "full")


if __name__ == "__main__":
    unittest.main()
