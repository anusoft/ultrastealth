import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from shopping_app.runner import (
    build_crawl_command,
    ensure_disk_space,
    finalize_partial,
    partial_directory,
    run_id_for_partial,
)


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


if __name__ == "__main__":
    unittest.main()
