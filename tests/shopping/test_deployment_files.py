import json
import unittest
from pathlib import Path


SYSTEMD_ROOT = Path("shopping_app/systemd")


class DeploymentFileTests(unittest.TestCase):
    def test_services_run_as_restricted_account(self):
        for name in ("shopping-scheduler.service", "shopping-crawl@.service"):
            with self.subTest(name=name):
                text = (SYSTEMD_ROOT / name).read_text()
                self.assertIn("User=shopping", text)
                self.assertIn("Group=shopping", text)
                self.assertIn("NoNewPrivileges=true", text)
                self.assertIn("ProtectSystem=strict", text)
                self.assertIn("ReadWritePaths=/shopping/", text)

    def test_scheduler_timer_runs_every_fifteen_minutes(self):
        text = (SYSTEMD_ROOT / "shopping-scheduler.timer").read_text()

        self.assertIn("OnUnitInactiveSec=15min", text)
        self.assertIn("Persistent=true", text)

    def test_package_installs_scrapling_and_wreq(self):
        package = json.loads(
            Path("shopping_app/deploy/package.json").read_text(encoding="utf-8")
        )

        self.assertEqual(package["dependencies"]["scrapling-js"], "github:anusoft/scrapling-js")
        self.assertEqual(package["dependencies"]["wreq-js"], "2.3.1")

    def test_bootstrap_creates_peer_matched_role_and_database(self):
        text = Path("shopping_app/deploy/bootstrap-remote.sh").read_text()

        self.assertIn("CREATE ROLE shopping LOGIN", text)
        self.assertIn("CREATE ROLE shopping_owner NOLOGIN", text)
        self.assertIn("createdb --owner=shopping_owner shopping", text)
        self.assertIn("systemctl daemon-reload", text)
        self.assertNotIn("enable --now shopping-scheduler.timer", text)


if __name__ == "__main__":
    unittest.main()
