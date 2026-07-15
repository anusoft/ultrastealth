import unittest

from shopping_app.manifest import load_manifest, run_args


EXPECTED_SITES = {
    "advice",
    "allonline",
    "b2s",
    "bigc",
    "bnbhome",
    "boots",
    "central",
    "dohome",
    "globalhouse",
    "gourmetmarket",
    "ihavecpu",
    "jib",
    "lotuss",
    "makro",
    "ofm",
    "powerbuy",
    "supersports",
    "thaiwatsadu",
    "tops",
    "villamarket",
    "watsons",
}


class ManifestTests(unittest.TestCase):
    def test_manifest_has_exact_marketplaces(self):
        manifest = load_manifest()

        self.assertEqual(set(manifest), EXPECTED_SITES)

    def test_every_marketplace_has_required_fields(self):
        manifest = load_manifest()

        for slug, site in manifest.items():
            with self.subTest(site=slug):
                self.assertEqual(site["slug"], slug)
                self.assertTrue(site["entrypoint"].endswith(f"/{slug}.mjs"))
                self.assertIsInstance(site["smoke_args"], list)
                self.assertIsInstance(site["full_args"], list)
                self.assertGreater(site["interval_seconds"], 0)
                self.assertIsInstance(site["enabled"], bool)

    def test_full_args_are_unlimited_and_resumable(self):
        args = run_args(load_manifest()["watsons"], "full")

        self.assertIn("--resume", args)
        for flag in (
            "--category-limit",
            "--page-limit",
            "--product-limit",
            "--review-page-limit",
            "--review-limit",
        ):
            self.assertEqual(args[args.index(flag) + 1], "0")

    def test_unknown_mode_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "unsupported crawl mode"):
            run_args(load_manifest()["advice"], "overnight")


if __name__ == "__main__":
    unittest.main()
