import unittest
import os
import socket
from pathlib import Path
from tempfile import TemporaryDirectory

from shopping_app.cli import active_lock_names, build_parser, initial_run_id


class CliTests(unittest.TestCase):
    def test_crawl_command_defaults_to_full_mode(self):
        args = build_parser().parse_args(["crawl", "advice"])

        self.assertEqual(args.command, "crawl")
        self.assertEqual(args.site, "advice")
        self.assertEqual(args.mode, "full")

    def test_ingest_requires_site_and_path(self):
        args = build_parser().parse_args(
            ["ingest", "--site", "watsons", "--path", "/shopping/imports/initial/watsons"]
        )

        self.assertEqual(args.site, "watsons")
        self.assertEqual(args.path, "/shopping/imports/initial/watsons")
        self.assertEqual(args.mode, "initial")

    def test_export_and_health_commands_are_available(self):
        parser = build_parser()

        for command in (
            "migrate",
            "schedule",
            "health",
            "queue-all",
            "export-baseline",
            "export-incremental",
            "import-bundle",
        ):
            with self.subTest(command=command):
                args = [command]
                if command == "import-bundle":
                    args.append("/tmp/bundle")
                self.assertEqual(parser.parse_args(args).command, command)

    def test_initial_import_run_id_is_stable_for_site_and_path(self):
        path = Path("/shopping/imports/initial/advice")

        first = initial_run_id("advice", path)
        second = initial_run_id("advice", path)

        self.assertEqual(first, second)
        self.assertNotEqual(first, initial_run_id("bigc", path))

    def test_active_locks_ignore_stale_pid_files(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            locks = root / "state" / "locks"
            locks.mkdir(parents=True)
            (locks / "advice.lock").write_text(
                f"pid={os.getpid()} host={socket.gethostname()}\n"
            )
            (locks / "bigc.lock").write_text(
                f"pid=999999999 host={socket.gethostname()}\n"
            )

            self.assertEqual(active_lock_names(root), ["advice"])


if __name__ == "__main__":
    unittest.main()
