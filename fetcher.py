"""
Ultrastealth Browser Automation
================================
Maximum stealth browser using rebrowser-playwright (CDP leak fix) +
Xvfb headed mode + enhanced JS bypasses + consistent fingerprint profile.

Combines all known anti-detection techniques for 95%+ pass rate on bot detection sites.

Usage:
    from web.ultrastealth import UltrastealthFetcher

    async with UltrastealthFetcher() as fetcher:
        html = await fetcher.fetch("https://example.com")
        # Or with page action callback:
        html = await fetcher.fetch("https://example.com", page_action=my_action)
"""

import asyncio
import contextlib
import io
import logging
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Optional

log = logging.getLogger("ultrastealth")

# Cloudflare challenge iframe URL pattern (from Scrapling)
_CF_PATTERN = re.compile(r"^https://challenges\.cloudflare\.com/cdn-cgi/challenge-platform/")

# Directory containing our enhanced JS bypass scripts
BYPASSES_DIR = Path(__file__).parent / "bypasses"

# Xvfb display number
XVFB_DISPLAY = os.environ.get("ULTRASTEALTH_DISPLAY", ":99")
FALLBACK_SCREEN_SIZE = (1440, 900)
MIN_WINDOW_SIZE = (800, 600)
WINDOW_MARGIN = (80, 160)

# Chrome/Chromium executable paths. Prefer real Google Chrome for stronger
# navigator.userAgentData brand parity; keep Chromium as an explicit fallback.
CHROME_PATHS = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Google Chrome Beta.app/Contents/MacOS/Google Chrome Beta",
    "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
]

DEFAULT_RUNNER = "chrome+default-profile"
DEFAULT_PROFILE_DIRECTORY = "Default"


def _find_chrome() -> Optional[str]:
    """Find a Chrome/Chromium binary on the system."""
    for path in CHROME_PATHS:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    # Fall back to shutil.which
    for name in ["google-chrome-stable", "google-chrome", "chromium", "chromium-browser"]:
        found = shutil.which(name)
        if found:
            return found
    return None


def _is_macos() -> bool:
    return sys.platform == "darwin"


def _is_linux() -> bool:
    return sys.platform.startswith("linux")


def _default_chromium_user_data_dir() -> Optional[str]:
    if _is_macos():
        return str(Path.home() / "Library/Application Support/Chromium")
    if _is_linux():
        return str(Path.home() / ".config/chromium")
    if sys.platform.startswith("win"):
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            return str(Path(local_app_data) / "Chromium/User Data")
    return None


def _default_google_chrome_user_data_dir() -> Optional[str]:
    if _is_macos():
        return str(Path.home() / "Library/Application Support/Google/Chrome")
    if _is_linux():
        return str(Path.home() / ".config/google-chrome")
    if sys.platform.startswith("win"):
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            return str(Path(local_app_data) / "Google/Chrome/User Data")
    return None


def _same_path(left: str, right: str) -> bool:
    return os.path.normcase(os.path.normpath(os.path.expanduser(left))) == os.path.normcase(
        os.path.normpath(os.path.expanduser(right))
    )


def _normalize_chromium_user_data_dir(user_data_dir: Optional[str]) -> Optional[str]:
    if user_data_dir is None:
        return None
    return os.path.expanduser(user_data_dir)


def _normalize_runner(runner: Optional[str]) -> str:
    value = runner or DEFAULT_RUNNER
    return value.strip().lower().replace("_", "-").replace(" ", "-")


def _runner_uses_default_profile(runner: str) -> bool:
    return runner in {
        "chrome",
        "chrome+default",
        "chrome+default-profile",
        "chrome:default-profile",
        "chrome-default-profile",
        "chromium",
        "chromium+default",
        "chromium+default-profile",
        "chromium:default-profile",
        "chromium-default-profile",
    }


def _default_chrome_user_data_dir() -> Optional[str]:
    """Return the OS default user-data root for the selected runner."""
    override = os.environ.get("ULTRASTEALTH_USER_DATA_DIR")
    if override:
        return _normalize_chromium_user_data_dir(override)

    return _default_google_chrome_user_data_dir()


def _default_user_data_dir_for_runner(runner: str) -> Optional[str]:
    if runner.startswith("chromium"):
        return _default_chromium_user_data_dir()
    return _default_google_chrome_user_data_dir()


def _should_run_headless(headless: bool) -> bool:
    """Choose Playwright's headless flag for the current platform."""
    if headless:
        return True
    if _is_macos():
        # macOS uses WindowServer instead of DISPLAY, so absence of DISPLAY does
        # not mean headed Chrome is unavailable.
        return False
    return not os.environ.get("DISPLAY")


def _parse_size(value: str) -> Optional[tuple[int, int]]:
    match = re.search(r"(\d+)\s*[x,]\s*(\d+)", value)
    if not match:
        return None
    width, height = int(match.group(1)), int(match.group(2))
    if width <= 0 or height <= 0:
        return None
    return width, height


def _host_screen_size() -> Optional[tuple[int, int]]:
    override = os.environ.get("ULTRASTEALTH_SCREEN_SIZE")
    if override:
        return _parse_size(override)

    if _is_macos():
        try:
            result = subprocess.run(
                [
                    "osascript",
                    "-e",
                    'tell application "Finder" to get bounds of window of desktop',
                ],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if result.returncode == 0:
                parts = [int(part.strip()) for part in result.stdout.split(",") if part.strip()]
                if len(parts) == 4:
                    width = parts[2] - parts[0]
                    height = parts[3] - parts[1]
                    if width > 0 and height > 0:
                        return width, height
        except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
            return None

    if _is_linux():
        command = ["xdpyinfo"]
        display = os.environ.get("DISPLAY")
        if display:
            command.extend(["-display", display])
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=2)
            if result.returncode == 0:
                match = re.search(r"dimensions:\s*(\d+)x(\d+)\s+pixels", result.stdout)
                if match:
                    return int(match.group(1)), int(match.group(2))
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return None

    return None


def _fit_window_size(screen_size: tuple[int, int]) -> tuple[int, int]:
    screen_width, screen_height = screen_size
    margin_width, margin_height = WINDOW_MARGIN
    min_width, min_height = MIN_WINDOW_SIZE
    width = min(max(min_width, screen_width - margin_width), screen_width)
    height = min(max(min_height, screen_height - margin_height), screen_height)
    return width, height


def _browser_window_dimensions() -> tuple[tuple[int, int], tuple[int, int]]:
    screen_size = _host_screen_size() or FALLBACK_SCREEN_SIZE
    return screen_size, _fit_window_size(screen_size)


def _load_patch_rebrowser():
    try:
        from . import patch_rebrowser
    except ImportError:
        import patch_rebrowser
    return patch_rebrowser


def _ensure_rebrowser_patched() -> bool:
    try:
        patcher = _load_patch_rebrowser()
        if patcher.is_patched():
            return True

        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            rc = patcher.run("apply")
        if rc == 0 and patcher.is_patched():
            log.info("Applied rebrowser driver fingerprint patch")
            return True

        log.warning(
            "rebrowser driver fingerprint patch could not be applied; "
            "__pwInitScripts / UtilityScript leaks may remain detectable. %s",
            output.getvalue().strip(),
        )
    except Exception:
        log.debug("Could not verify/apply rebrowser driver fingerprint patch", exc_info=True)
    return False


def _ensure_xvfb() -> Optional[subprocess.Popen]:
    """Start Xvfb if not already running and DISPLAY is not set."""
    if not _is_linux():
        return None

    display = os.environ.get("DISPLAY", "")
    if display and display != XVFB_DISPLAY:
        # Real display available (e.g., desktop environment)
        return None

    # Check if Xvfb is already running on our display
    xvfb_bin = shutil.which("Xvfb")
    if not xvfb_bin:
        log.warning("Xvfb not found — running in headless mode (less stealthy)")
        return None

    # Check if already running
    try:
        result = subprocess.run(
            ["xdpyinfo", "-display", XVFB_DISPLAY],
            capture_output=True, timeout=2
        )
        if result.returncode == 0:
            os.environ["DISPLAY"] = XVFB_DISPLAY
            return None  # Already running
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Start Xvfb
    proc = subprocess.Popen(
        [xvfb_bin, XVFB_DISPLAY, "-screen", "0", "1920x1080x24", "-nolisten", "tcp"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    os.environ["DISPLAY"] = XVFB_DISPLAY
    # Give it a moment to start
    import time
    time.sleep(0.3)
    log.info(f"Started Xvfb on {XVFB_DISPLAY}")
    return proc


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _chrome_singleton_pid(user_data_dir: str) -> Optional[int]:
    lock_path = Path(user_data_dir) / "SingletonLock"
    if not os.path.lexists(lock_path):
        return None
    try:
        target = os.readlink(lock_path)
    except OSError:
        return None
    try:
        return int(target.rsplit("-", 1)[1])
    except (IndexError, ValueError):
        return None


def _cleanup_stale_chrome_singletons(user_data_dir: str) -> bool:
    pid = _chrome_singleton_pid(user_data_dir)
    if pid is None or _pid_exists(pid):
        return False

    removed = False
    for name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
        path = Path(user_data_dir) / name
        if os.path.lexists(path):
            try:
                path.unlink()
                removed = True
            except OSError:
                log.warning("Could not remove stale browser singleton: %s", path)
    return removed


def _load_bypass_scripts() -> str:
    """Load and concatenate the JS bypass scripts. Default: OFF.

    Benchmark evidence (bot_benchmark.py, 2026-06): on this real-Chrome-on-Linux stack
    the clean browser fingerprint is already consistent and beats the spoofed one on
    EVERY fingerprint site (devbrowserinfo 21/21 vs 19/21, fingerprint-scan pass vs
    95/100 bot-risk, sannysoft 31/31 vs 30/31, rebrowser 6/10 vs 5/10). The JS spoofs
    (Windows-ish GPU, instance-level navigator overrides, fragile worker patch) add more
    inconsistencies than they hide. The driver-level stealth that actually wins — real
    Chrome + Xvfb headful + rebrowser patch + alwaysIsolated — does not live here.

    These scripts were also silently broken for a long time (a syntax error in
    webdriver_fully.js made V8 reject the whole init script), so production has de-facto
    run with them OFF. This makes that explicit. They are now repaired and undetectable
    (Proxy-based native masking, OS-consistent values, IIFE-isolated) for the targets
    where the GPU/canvas spoofs are specifically needed — opt in with
    ULTRASTEALTH_BYPASSES=on.
    """
    if os.environ.get("ULTRASTEALTH_BYPASSES", "off").lower() not in ("on", "1", "true"):
        return ""

    scripts = []

    # Load in specific order (dependencies first)
    order = [
        "_native_mask.js",
        "webdriver_fully.js",
        "window_chrome.js",
        "navigator_plugins.js",
        "playwright_fingerprint.js",
        "screen_props.js",
        "notification_permission.js",
        "runtime_enable_fix.js",
        "hardware_profile.js",
        "webgl_spoof.js",
        "canvas_noise.js",
        "audio_noise.js",
        "worker_consistency.js",
    ]

    for filename in order:
        path = BYPASSES_DIR / filename
        if not path.exists():
            log.warning(f"Bypass script not found: {path}")
            continue
        body = path.read_text()
        if filename == "_native_mask.js":
            # Must stay at the shared top scope so its `const __usMask` is visible to
            # every later bypass. (It is a lexical binding, not a window property —
            # invisible to page JS.)
            scripts.append(f"// === {filename} ===\n{body}")
        else:
            # Isolate each bypass in its own IIFE + try/catch. The scripts are
            # concatenated into ONE init script, so without this:
            #   - a thrown exception in any bypass would abort the whole remaining chain;
            #   - a top-level `return` (window_chrome.js has several, reached when
            #     window.chrome is absent — e.g. insecure origins) would return from the
            #     entire init function, silently skipping every later spoof.
            # An IIFE contains both `return` and `throw`, making the spoofs independent.
            scripts.append(
                f"// === {filename} ===\n(function(){{\ntry {{\n{body}\n}} catch (e) {{}}\n}})();"
            )

    return "\n\n".join(scripts)


# Stealth Chromium flags (subset of Scrapling's STEALTH_ARGS + our additions)
STEALTH_FLAGS = [
    "--test-type",
    "--lang=en-US",
    "--mute-audio",
    "--disable-sync",
    "--disable-logging",
    "--enable-async-dns",
    "--accept-lang=en-US",
    "--use-mock-keychain",
    "--disable-translate",
    "--disable-voice-input",
    "--window-position=0,0",
    "--disable-wake-on-wifi",
    "--ignore-gpu-blocklist",
    "--enable-tcp-fast-open",
    "--enable-web-bluetooth",
    "--disable-cloud-import",
    "--disable-print-preview",
    "--disable-dev-shm-usage",
    "--disable-component-update",
    "--metrics-recording-only",
    "--disable-crash-reporter",
    "--disable-partial-raster",
    "--disable-gesture-typing",
    "--disable-checker-imaging",
    "--disable-prompt-on-repost",
    "--force-color-profile=srgb",
    "--font-render-hinting=none",
    "--aggressive-cache-discard",
    "--disable-cookie-encryption",
    "--disable-domain-reliability",
    "--disable-threaded-animation",
    "--disable-threaded-scrolling",
    "--enable-simple-cache-backend",
    "--disable-background-networking",
    "--disable-desktop-notifications",
    "--enable-surface-synchronization",
    "--disable-image-animation-resync",
    "--disable-renderer-backgrounding",
    "--disable-ipc-flooding-protection",
    "--prerender-from-omnibox=disabled",
    "--safebrowsing-disable-auto-update",
    "--disable-offer-upload-credit-cards",
    "--disable-background-timer-throttling",
    "--disable-new-content-rendering-timeout",
    "--run-all-compositor-stages-before-draw",
    "--disable-client-side-phishing-detection",
    "--disable-backgrounding-occluded-windows",
    "--disable-layer-tree-host-memory-pressure",
    "--no-first-run",
    "--no-service-autorun",
    "--no-default-browser-check",
    "--no-pings",
    "--noerrdialogs",
    "--disable-default-apps",
    "--disable-datasaver-prompt",
    "--disable-external-intent-requests",
    "--disable-focus-on-load",
    "--disable-infobars",
    "--disable-search-engine-choice-screen",
    "--disable-window-activation",
    "--allow-pre-commit-input",
    "--hide-crash-restore-bubble",
    "--install-autogenerated-theme=0,0,0",
    "--silent-debugger-extension-api",
    "--simulate-outdated-no-au=Tue, 31 Dec 2099 23:59:59 GMT",
    "--suppress-message-center-popups",
    "--unsafely-disable-devtools-self-xss-warnings",
    "--autoplay-policy=user-gesture-required",
    "--disable-offer-store-unmasked-wallet-cards",
    "--disable-blink-features=AutomationControlled",
    "--disable-component-extensions-with-background-pages",
    "--disable-extensions-http-throttling",
    "--extensions-on-chrome-urls",
    "--enable-features=NetworkService,NetworkServiceInProcess",
    "--blink-settings=primaryHoverType=2,availableHoverTypes=2,primaryPointerType=4,availablePointerTypes=4",
    "--disable-features=AudioServiceOutOfProcess,TranslateUI,BlinkGenPropertyTrees",
    # WebRTC leak prevention
    "--webrtc-ip-handling-policy=disable_non_proxied_udp",
    "--force-webrtc-ip-handling-policy",
]

# Flags to exclude from Chrome's defaults
HARMFUL_FLAGS = [
    "--enable-automation",
    "--disable-popup-blocking",
    "--disable-component-update",
    "--disable-default-apps",
    "--disable-extensions",
]


class UltrastealthFetcher:
    """Maximum stealth async browser automation.

    Combines:
    - rebrowser-playwright (fixes Runtime.Enable CDP leak)
    - Xvfb headed mode (eliminates headless detection)
        - Real Chrome/Chromium binary
    - Enhanced JS bypasses (worker consistency, canvas/WebGL/audio noise, hardware profile)
    - Consistent device fingerprint profile
    """

    def __init__(
        self,
        headless: bool = False,
        proxy: Optional[dict] = None,
        timeout: int = 30000,
        user_data_dir: Optional[str] = None,
        executable_path: Optional[str] = None,
        runner: Optional[str] = None,
        profile_directory: Optional[str] = None,
        fallback_to_temp_profile: bool = True,
    ):
        self.headless = headless
        self.proxy = proxy
        self.timeout = timeout
        raw_env_user_data_dir = os.environ.get("ULTRASTEALTH_USER_DATA_DIR")
        env_user_data_dir = _normalize_chromium_user_data_dir(raw_env_user_data_dir)
        env_profile_directory = os.environ.get("ULTRASTEALTH_PROFILE_DIRECTORY")
        raw_env_runner = os.environ.get("ULTRASTEALTH_RUNNER")
        env_runner = raw_env_runner
        self.runner = _normalize_runner(runner or env_runner)
        self.explicit_chrome_profile_requested = any(
            value is not None
            for value in (
                user_data_dir,
                profile_directory,
                runner,
                env_user_data_dir,
                env_profile_directory,
                env_runner,
            )
        )
        self.fallback_to_temp_profile = fallback_to_temp_profile
        self.profile_directory = (
            profile_directory
            or env_profile_directory
            or DEFAULT_PROFILE_DIRECTORY
        )
        self.user_data_dir = _normalize_chromium_user_data_dir(user_data_dir) or env_user_data_dir
        self.executable_path = executable_path or _find_chrome()
        self.uses_default_chrome_profile = False
        self.owns_user_data_dir = False
        self._playwright = None
        self._browser = None
        self._context = None
        self._xvfb_proc = None
        self._temp_dir = None
        self._bypass_scripts = _load_bypass_scripts()

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, *args):
        await self.close()

    async def start(self):
        """Launch browser with maximum stealth configuration."""
        _ensure_rebrowser_patched()

        # Start Xvfb if needed (for headed mode without a display)
        if not self.headless and _is_linux():
            self._xvfb_proc = _ensure_xvfb()

        # Run page.evaluate in an ISOLATED world by default (maximum stealth):
        # this defeats main-world execution detection (e.g. rebrowser's
        # mainWorldExecution probe). Trade-off: evaluate can read the shared DOM
        # but not main-world JS globals (window.someAppState). Override by
        # exporting REBROWSER_PATCHES_RUNTIME_FIX_MODE=addBinding if you need
        # main-world JS access. setdefault → an explicit env value always wins.
        os.environ.setdefault("REBROWSER_PATCHES_RUNTIME_FIX_MODE", "alwaysIsolated")

        # Use rebrowser-playwright for CDP leak fix
        from rebrowser_playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()

        # Prepare launch args
        use_headless = _should_run_headless(self.headless)

        # Opt-in CDP endpoint: set ULTRASTEALTH_CDP_PORT to expose a remote
        # debugging port so an external Playwright client (e.g. the
        # craft-scraper authoring loop) can connect_over_cdp and drive THIS
        # stealth browser. Default off — behavior unchanged when unset.
        stealth_flags = list(STEALTH_FLAGS)
        _cdp_port = os.environ.get("ULTRASTEALTH_CDP_PORT")
        if _cdp_port:
            stealth_flags.append("--remote-debugging-address=127.0.0.1")
            stealth_flags.append(f"--remote-debugging-port={_cdp_port}")
            log.info(f"CDP debugging endpoint enabled on 127.0.0.1:{_cdp_port}")

        screen_size, window_size = _browser_window_dimensions()
        screen_width, screen_height = screen_size
        window_width, window_height = window_size
        stealth_flags.append(f"--window-size={window_width},{window_height}")

        # Create/select the user-data directory before launch args are finalized.
        # The default runner uses the user's regular browser profile root and
        # selects the "Default" profile directory inside it.
        if not self.user_data_dir:
            default_profile_root = (
                _default_user_data_dir_for_runner(self.runner)
                if _runner_uses_default_profile(self.runner)
                else None
            )
            if default_profile_root:
                self.user_data_dir = default_profile_root
                self.uses_default_chrome_profile = True
            else:
                self._temp_dir = tempfile.mkdtemp(prefix="ultrastealth_")
                self.user_data_dir = self._temp_dir
                self.owns_user_data_dir = True

        profile_directory_arg = None
        if self.profile_directory and not self.owns_user_data_dir:
            profile_directory_arg = f"--profile-directory={self.profile_directory}"
            stealth_flags.append(profile_directory_arg)

        if self.user_data_dir and not self.owns_user_data_dir:
            if _cleanup_stale_chrome_singletons(self.user_data_dir):
                log.warning(
                    "Removed stale browser singleton files from %s before launch",
                    self.user_data_dir,
                )

        launch_args = {
            "args": stealth_flags,
            "ignore_default_args": HARMFUL_FLAGS,
            "headless": use_headless,
        }

        if self.executable_path:
            launch_args["executable_path"] = self.executable_path
            log.info(f"Using Chrome/Chromium: {self.executable_path}")
        else:
            log.info("Using bundled Chromium (no system Chrome/Chromium found)")

        # Launch persistent context (more realistic than incognito)
        context_opts: dict[str, Any] = {
            "color_scheme": "dark",
            "device_scale_factor": 2,
            "is_mobile": False,
            "has_touch": False,
            "service_workers": "allow",
            "ignore_https_errors": True,
            "screen": {"width": screen_width, "height": screen_height},
            "viewport": {"width": window_width, "height": window_height},
            "permissions": ["geolocation", "notifications"],
            "locale": "en-US",
            "timezone_id": "America/New_York",
        }

        if self.proxy:
            context_opts["proxy"] = self.proxy

        if self.uses_default_chrome_profile:
            log.info(
                "Using browser default profile: %s (profile directory: %s)",
                self.user_data_dir,
                self.profile_directory,
            )

        try:
            self._context = await self._playwright.chromium.launch_persistent_context(
                self.user_data_dir,
                **launch_args,
                **context_opts,
            )
        except Exception as exc:
            if (
                not self.uses_default_chrome_profile
                or not self.fallback_to_temp_profile
                or self.explicit_chrome_profile_requested
            ):
                raise RuntimeError(
                    "Could not launch requested browser profile "
                    f"{self.profile_directory!r} from user data dir "
                    f"{self.user_data_dir!r}. Close all Chrome/Chromium windows "
                    "using that user-data dir, then restart the MCP server. "
                    f"Original error: {exc}"
                ) from exc

            log.warning(
                "Default browser profile failed to launch; retrying with a temporary "
                "profile. Close Chrome/Chromium or set ULTRASTEALTH_RUNNER=chrome+temp-profile "
                "to avoid this fallback. Error: %s",
                exc,
            )
            self._temp_dir = tempfile.mkdtemp(prefix="ultrastealth_")
            self.user_data_dir = self._temp_dir
            self.owns_user_data_dir = True
            self.uses_default_chrome_profile = False

            fallback_launch_args = dict(launch_args)
            fallback_args = list(launch_args["args"])
            if profile_directory_arg:
                fallback_args = [arg for arg in fallback_args if arg != profile_directory_arg]
            fallback_launch_args["args"] = fallback_args

            try:
                self._context = await self._playwright.chromium.launch_persistent_context(
                    self.user_data_dir,
                    **fallback_launch_args,
                    **context_opts,
                )
            except Exception:
                if self._temp_dir and os.path.exists(self._temp_dir):
                    shutil.rmtree(self._temp_dir, ignore_errors=True)
                self._temp_dir = None
                self.user_data_dir = None
                self.owns_user_data_dir = False
                raise

        # Inject stealth scripts into all new pages
        await self._context.add_init_script(self._bypass_scripts)

        log.info(f"Ultrastealth browser started (headless={use_headless})")

    async def close(self):
        """Clean up browser and Xvfb."""
        if self._context:
            try:
                await self._context.close()
            except Exception:
                pass
            self._context = None

        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

        if self._xvfb_proc:
            self._xvfb_proc.terminate()
            self._xvfb_proc = None

        if self._temp_dir and os.path.exists(self._temp_dir):
            shutil.rmtree(self._temp_dir, ignore_errors=True)
            self._temp_dir = None

    @staticmethod
    def _detect_cloudflare(page_content: str) -> Optional[str]:
        """Detect Cloudflare challenge type from page HTML.

        Returns "non-interactive", "managed", "interactive", "embedded" or None.
        Port of Scrapling's StealthySessionMixin._detect_cloudflare.
        """
        for ctype in ("non-interactive", "managed", "interactive"):
            if f"cType: '{ctype}'" in page_content:
                return ctype
        # Embedded Turnstile widget (script tag loaded)
        if 'challenges.cloudflare.com/turnstile/v' in page_content:
            return "embedded"
        return None

    async def solve_cloudflare(self, page, max_wait_secs: float = 20.0) -> bool:
        """Auto-solve Cloudflare Turnstile/Interstitial challenges.

        Ports Scrapling StealthySession._cloudflare_solver logic. Detects the
        challenge type, waits for non-interactive challenges to auto-clear, or
        clicks the Turnstile widget for interactive/managed types. Retries
        recursively if the challenge re-appears (CF sometimes shows it twice).

        Args:
            page: Playwright page object (after goto)
            max_wait_secs: Overall ceiling for the solving loop

        Returns:
            True if no CF challenge detected after solving, False if still blocked.
        """
        # Let the page settle so CF scripts can initialize
        try:
            await page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass

        page_content = await page.evaluate("document.documentElement.outerHTML")
        challenge_type = self._detect_cloudflare(page_content)
        if not challenge_type:
            log.debug("No Cloudflare challenge detected")
            return True  # already through

        log.info(f"Cloudflare challenge detected: {challenge_type}")

        if challenge_type == "non-interactive":
            # Wait for the "Just a moment..." page to disappear
            deadline = asyncio.get_event_loop().time() + max_wait_secs
            while asyncio.get_event_loop().time() < deadline:
                content = await page.evaluate("document.documentElement.outerHTML")
                if "<title>Just a moment..." not in content:
                    log.info("Cloudflare non-interactive challenge cleared")
                    return True
                await asyncio.sleep(1)
            log.warning("Cloudflare non-interactive challenge timeout")
            return False

        # Interactive / managed / embedded: need to click the widget
        box_selector = "#cf_turnstile div, #cf-turnstile div, .turnstile>div>div"
        if challenge_type != "embedded":
            box_selector = ".main-content p+div>div>div"
            # Wait for the verify spinner to disappear
            spinner_deadline = asyncio.get_event_loop().time() + 10
            while asyncio.get_event_loop().time() < spinner_deadline:
                content = await page.evaluate("document.documentElement.outerHTML")
                if "Verifying you are human." not in content:
                    break
                await asyncio.sleep(0.5)

        # Find the Turnstile iframe
        outer_box = None
        iframe_frame = None
        for frame in page.frames:
            if _CF_PATTERN.match(frame.url or ""):
                iframe_frame = frame
                break

        if iframe_frame is not None:
            try:
                frame_elem = await iframe_frame.frame_element()
                # Wait for iframe to be visible
                visible_deadline = asyncio.get_event_loop().time() + 10
                while asyncio.get_event_loop().time() < visible_deadline:
                    if await frame_elem.is_visible():
                        break
                    await asyncio.sleep(0.5)
                outer_box = await frame_elem.bounding_box()
            except Exception as e:
                log.debug(f"Could not get iframe bounding box: {e}")

        if not outer_box:
            # If iframe not found, check if already solved
            content = await page.evaluate("document.documentElement.outerHTML")
            if "<title>Just a moment..." not in content:
                log.info("Cloudflare challenge auto-cleared")
                return True
            # Fall back to locator on the outer box
            try:
                locator = page.locator(box_selector).last
                outer_box = await locator.bounding_box()
            except Exception as e:
                log.warning(f"Could not locate Turnstile widget: {e}")
                return False

        if not outer_box:
            return False

        # Click inside the Turnstile checkbox area with randomized offset
        cx = outer_box["x"] + random.randint(26, 28)
        cy = outer_box["y"] + random.randint(25, 27)
        await page.mouse.click(cx, cy, delay=random.randint(100, 200), button="left")

        # Wait for network to settle after click
        try:
            await page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass

        # Verify the challenge cleared
        if challenge_type != "embedded":
            clear_deadline = asyncio.get_event_loop().time() + max_wait_secs
            while asyncio.get_event_loop().time() < clear_deadline:
                content = await page.evaluate("document.documentElement.outerHTML")
                if "<title>Just a moment..." not in content:
                    break
                await asyncio.sleep(0.1)

        # Final check — recurse once if still blocked (CF sometimes shows 2x)
        content = await page.evaluate("document.documentElement.outerHTML")
        if "<title>Just a moment..." in content:
            log.info("Cloudflare still present — solving again")
            return await self.solve_cloudflare(page, max_wait_secs=max_wait_secs)

        log.info("Cloudflare challenge solved")
        return True

    async def fetch(
        self,
        url: str,
        wait_secs: float = 3.0,
        page_action: Optional[Callable] = None,
        solve_cloudflare: bool = False,
    ) -> str:
        """Fetch a URL with maximum stealth, return HTML content.

        Args:
            url: URL to fetch
            wait_secs: Seconds to wait after navigation for JS to execute
            page_action: Optional async callback(page) for custom interaction
            solve_cloudflare: If True, auto-solve Cloudflare Turnstile/Interstitial challenges
        """
        if not self._context:
            raise RuntimeError("Browser not started. Use 'async with UltrastealthFetcher()' or call start().")

        page = await self._context.new_page()
        try:
            # Navigate
            await page.goto(url, wait_until="domcontentloaded", timeout=self.timeout)

            # Wait for content to load
            await asyncio.sleep(wait_secs)

            # Auto-solve Cloudflare if requested
            if solve_cloudflare:
                await self.solve_cloudflare(page)

            # Run custom page action if provided
            if page_action:
                await page_action(page)

            # Extract HTML using evaluate (avoids page.content() hang on SPAs)
            html = await page.evaluate("document.documentElement.outerHTML")
            return html
        finally:
            await page.close()

    async def fetch_and_evaluate(
        self,
        url: str,
        js_expression: str,
        wait_secs: float = 3.0,
        pre_eval_js: Optional[list[str]] = None,
        solve_cloudflare: bool = False,
    ) -> Any:
        """Fetch URL and evaluate JavaScript, return the result.

        Args:
            url: URL to fetch
            js_expression: JS expression to evaluate (should be a function body returning a value)
            wait_secs: Seconds to wait after navigation
            pre_eval_js: Optional list of JS expressions to run before the main evaluation
            solve_cloudflare: If True, auto-solve Cloudflare Turnstile/Interstitial challenges
        """
        if not self._context:
            raise RuntimeError("Browser not started.")

        page = await self._context.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=self.timeout)
            await asyncio.sleep(wait_secs)

            # Auto-solve Cloudflare if requested
            if solve_cloudflare:
                await self.solve_cloudflare(page)

            # Run pre-evaluation scripts (e.g., scroll, click)
            if pre_eval_js:
                for expr in pre_eval_js:
                    try:
                        await page.evaluate(expr)
                    except Exception as e:
                        log.debug(f"Pre-eval script failed: {e}")
                await asyncio.sleep(wait_secs)

            # Evaluate main expression
            result = await page.evaluate(js_expression)
            return result
        finally:
            await page.close()
