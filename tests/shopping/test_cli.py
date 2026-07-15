import unittest

from shopping_app.cli import build_parser


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


if __name__ == "__main__":
    unittest.main()
