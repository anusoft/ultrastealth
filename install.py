"""One-step installer for Ultrastealth runtime dependencies."""

from __future__ import annotations

import argparse
import subprocess
import sys

try:
    from . import patch_rebrowser
except ImportError:
    import patch_rebrowser


def _rebrowser_playwright_command() -> list[str]:
    return [sys.executable, "-m", "rebrowser_playwright"]


def _install_chromium() -> int:
    command = [*_rebrowser_playwright_command(), "install", "chromium"]
    print("Installing Chromium for rebrowser-playwright...")
    result = subprocess.run(command, check=False)
    return result.returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Install Ultrastealth runtime dependencies and apply the rebrowser patch."
    )
    parser.add_argument(
        "--skip-browser-install",
        action="store_true",
        help="Skip rebrowser_playwright's Chromium install step and only apply the driver patch.",
    )
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    browser_rc = 0
    if not args.skip_browser_install:
        browser_rc = _install_chromium()

    print("Applying rebrowser driver fingerprint patch...")
    patch_rc = patch_rebrowser.run("apply")

    return browser_rc or patch_rc


if __name__ == "__main__":
    raise SystemExit(main())
