import importlib
import os
import tempfile
import tomllib
import types
import unittest
from pathlib import Path
from unittest.mock import patch


class InstallCommandTests(unittest.TestCase):
    def test_install_command_installs_chromium_then_applies_patch(self):
        install = importlib.import_module("install")
        calls = []

        def run_browser_install(cmd, check=False):
            calls.append(("browser", cmd, check))
            return types.SimpleNamespace(returncode=0)

        def run_patch(mode):
            calls.append(("patch", mode))
            return 0

        with tempfile.TemporaryDirectory() as tmp:
            fake_path_script = Path(tmp) / "rebrowser_playwright"
            fake_path_script.write_text("#!/bin/sh\nexit 99\n")
            fake_path_script.chmod(0o755)

            with patch.dict(os.environ, {"PATH": tmp}), \
                 patch.object(install.sys, "executable", "/venv/bin/python"), \
                 patch.object(install.subprocess, "run", side_effect=run_browser_install), \
                 patch.object(install.patch_rebrowser, "run", side_effect=run_patch):
                self.assertEqual(install.main([]), 0)

        self.assertEqual(
            calls,
            [
                (
                    "browser",
                    ["/venv/bin/python", "-m", "rebrowser_playwright", "install", "chromium"],
                    False,
                ),
                ("patch", "apply"),
            ],
        )

    def test_install_command_still_patches_when_browser_install_is_skipped(self):
        install = importlib.import_module("install")

        with patch.object(install.subprocess, "run") as run, \
             patch.object(install.patch_rebrowser, "run", return_value=0) as patch_run:
            self.assertEqual(install.main(["--skip-browser-install"]), 0)

        run.assert_not_called()
        patch_run.assert_called_once_with("apply")

    def test_pyproject_exposes_one_step_install_command(self):
        pyproject = tomllib.loads(Path("pyproject.toml").read_text())

        self.assertEqual(
            pyproject["project"]["scripts"]["ultrastealth-install"],
            "ultrastealth.install:main",
        )

    def test_root_install_script_runs_one_step_installer(self):
        script = Path("install.sh")

        self.assertTrue(script.exists())
        self.assertTrue(os.access(script, os.X_OK))
        content = script.read_text()
        self.assertIn("ensurepip", content)
        self.assertIn("pip install -e", content)
        self.assertIn("-m ultrastealth.install", content)


if __name__ == "__main__":
    unittest.main()
