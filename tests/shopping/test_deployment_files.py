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
                self.assertIn("ProtectHome=read-only", text)
                self.assertIn("ReadWritePaths=/home/anu/shopping\n", text)
                self.assertIn("ReadOnlyPaths=/home/anu/shopping/app\n", text)

    def test_services_keep_partial_and_final_data_on_one_writable_mount(self):
        for name in ("shopping-scheduler.service", "shopping-crawl@.service"):
            with self.subTest(name=name):
                text = (SYSTEMD_ROOT / name).read_text()
                self.assertIn("ReadWritePaths=/home/anu/shopping\n", text)
                self.assertNotIn(
                    "ReadWritePaths=/home/anu/shopping/data /home/anu/shopping/partial",
                    text,
                )

    def test_services_use_anu_home_as_canonical_root(self):
        for name in ("shopping-scheduler.service", "shopping-crawl@.service"):
            with self.subTest(name=name):
                text = (SYSTEMD_ROOT / name).read_text()
                self.assertIn("WorkingDirectory=/home/anu/shopping/app", text)
                self.assertIn("Environment=SHOPPING_ROOT=/home/anu/shopping", text)
                self.assertIn("Environment=SHOPPING_APP_ROOT=/home/anu/shopping/app", text)
                self.assertIn("ExecStart=/home/anu/shopping/app/.venv/bin/python", text)

    def test_scheduler_timer_runs_every_fifteen_minutes(self):
        text = (SYSTEMD_ROOT / "shopping-scheduler.timer").read_text()

        self.assertIn("OnUnitInactiveSec=15min", text)
        self.assertIn("Persistent=true", text)

    def test_package_installs_scrapling_and_wreq(self):
        package = json.loads(
            Path("shopping_app/deploy/package.json").read_text(encoding="utf-8")
        )

        self.assertEqual(
            package["dependencies"]["scrapling-js"],
            "github:anusoft/scrapling-js#5900b5032212605932d06f870b87b9d811583159",
        )
        self.assertEqual(package["dependencies"]["wreq-js"], "2.3.1")
        self.assertEqual(package["trustedDependencies"], ["scrapling-js"])
        self.assertTrue(Path("shopping_app/deploy/bun.lock").is_file())

    def test_bootstrap_creates_peer_matched_role_and_database(self):
        text = Path("shopping_app/deploy/bootstrap-remote.sh").read_text()

        self.assertIn("SHOPPING_ROOT=${SHOPPING_ROOT:-/home/anu/shopping}", text)
        self.assertIn('setfacl -m u:shopping:--x "$(dirname "${SHOPPING_ROOT}")"', text)
        self.assertIn("CREATE ROLE shopping LOGIN", text)
        self.assertIn("CREATE ROLE shopping_owner NOLOGIN", text)
        self.assertIn("createdb --owner=shopping_owner shopping", text)
        self.assertIn("systemctl daemon-reload", text)
        self.assertNotIn("enable --now shopping-scheduler.timer", text)

    def test_bootstrap_builds_and_verifies_scrapling_runtime(self):
        text = Path("shopping_app/deploy/bootstrap-remote.sh").read_text()

        self.assertIn('node_modules/scrapling-js" build', text)
        self.assertIn('import("scrapling-js")', text)
        self.assertIn("bun-v1.3.14", text)
        self.assertIn("postgresql-client-17", text)
        self.assertIn("python3-venv zstd", text)
        self.assertIn("--frozen-lockfile", text)
        self.assertIn("runuser -u shopping", text)
        self.assertIn('PATH="${SHOPPING_HOME}/.bun/bin:', text)


if __name__ == "__main__":
    unittest.main()
