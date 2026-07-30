#!/usr/bin/env python3
"""
Bot Detection Benchmark Suite
==============================
Tests browser stealth across multiple bot detection sites and fetcher methods.
Outputs JSON results + formatted comparison tables.

Benchmarks UltrastealthFetcher against ~15 bot-detection / fingerprint sites.
Run under Xvfb (DISPLAY=:99). Some sites (pixelscan/incolumitas) only return a
verdict from a residential IP; cloudflare needs the Turnstile solver.

Usage:
    DISPLAY=:99 python3 bot_benchmark.py                       # Run all sites
    DISPLAY=:99 python3 bot_benchmark.py --sites sannysoft rebrowser
    python3 bot_benchmark.py --results results.json            # Custom output file
    python3 bot_benchmark.py --compare results.json            # Print table from a previous run
"""

import asyncio
import argparse
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class TestResult:
    name: str
    passed: bool | None  # None = skipped/untriggered
    value: str = ""
    severity: str = "info"  # info, pass, fail, warn, skip


@dataclass
class SiteResult:
    site: str
    method: str
    passed: int = 0
    failed: int = 0
    warned: int = 0
    skipped: int = 0
    total: int = 0
    error: str = ""
    elapsed_ms: int = 0
    tests: list = field(default_factory=list)
    raw: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Fetcher methods
# ---------------------------------------------------------------------------

METHODS = {
    "ultrastealth": "Ultrastealth (rebrowser + Xvfb)",
    "patchright": "Ultrastealth (patchright engine + Xvfb, opt-in alternative)",
    "lightpanda": "Lightpanda (CDP + Chrome-like UA + JS stealth shims)",
    "obscura": "Obscura (Rust CDP browser, native --stealth anti-detect)",
}

LIGHTPANDA_DEFAULT_USER_AGENT = (
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/149.0.0.0 Safari/537.36"
)

OBSCURA_DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
)

_LIGHTPANDA_STEALTH_JS = r"""(() => {
    const ua = __LIGHTPANDA_USER_AGENT__;
    const defineGetter = (obj, prop, getter) => {
        try { Object.defineProperty(obj, prop, { get: getter, configurable: true }); } catch (e) {}
    };

    defineGetter(Navigator.prototype, 'webdriver', () => false);
    defineGetter(Navigator.prototype, 'userAgent', () => ua);
    defineGetter(Navigator.prototype, 'languages', () => ['en-US', 'en']);
    defineGetter(Navigator.prototype, 'platform', () => 'MacIntel');
    defineGetter(Navigator.prototype, 'hardwareConcurrency', () => 8);
    defineGetter(Navigator.prototype, 'deviceMemory', () => 8);
    defineGetter(window, 'outerWidth', () => Math.max(window.innerWidth || 1280, 1432));
    defineGetter(window, 'outerHeight', () => Math.max((window.innerHeight || 720) + 88, 822));

    try {
        if (!window.Plugin) window.Plugin = function Plugin() {};
        Object.defineProperty(window.Plugin.prototype, Symbol.toStringTag, { value: 'Plugin' });
        window.Plugin.prototype.toString = () => '[object Plugin]';
    } catch (e) {}
    const fakePlugin = (name, filename, description) => {
        const plugin = { name, filename, description, length: 1 };
        plugin.toString = () => '[object Plugin]';
        try { Object.setPrototypeOf(plugin, window.Plugin.prototype); } catch (e) {}
        return plugin;
    };
    const fakePlugins = [
        fakePlugin('Chrome PDF Plugin', 'internal-pdf-viewer', 'Portable Document Format'),
        fakePlugin('Chrome PDF Viewer', 'mhjfbmdgcfjbbpaeojofohoefgiehjai', ''),
        fakePlugin('Native Client', 'internal-nacl-plugin', ''),
        fakePlugin('Widevine Content Decryption Module', 'widevinecdmadapter.plugin', ''),
        fakePlugin('Chromium PDF Viewer', 'chromium-pdf-viewer', ''),
    ];
    fakePlugins.item = index => fakePlugins[index] || null;
    fakePlugins.namedItem = name => Array.prototype.find.call(fakePlugins, plugin => plugin.name === name) || null;
    fakePlugins.refresh = () => {};
    try { Object.defineProperty(fakePlugins, Symbol.toStringTag, { value: 'PluginArray' }); } catch (e) {}
    try {
        if (!window.PluginArray) window.PluginArray = function PluginArray() {};
        Object.defineProperty(window.PluginArray.prototype, Symbol.toStringTag, { value: 'PluginArray' });
        Object.setPrototypeOf(fakePlugins, window.PluginArray.prototype);
    } catch (e) {}
    defineGetter(Navigator.prototype, 'plugins', () => fakePlugins);

    const fakeMimeTypes = [
        { type: 'application/pdf', suffixes: 'pdf', description: 'Portable Document Format' },
        { type: 'application/x-google-chrome-pdf', suffixes: 'pdf', description: 'Portable Document Format' },
    ];
    fakeMimeTypes.item = index => fakeMimeTypes[index] || null;
    fakeMimeTypes.namedItem = type => Array.prototype.find.call(fakeMimeTypes, mime => mime.type === type) || null;
    try { Object.defineProperty(fakeMimeTypes, Symbol.toStringTag, { value: 'MimeTypeArray' }); } catch (e) {}
    try {
        if (!window.MimeTypeArray) window.MimeTypeArray = function MimeTypeArray() {};
        Object.defineProperty(window.MimeTypeArray.prototype, Symbol.toStringTag, { value: 'MimeTypeArray' });
        Object.setPrototypeOf(fakeMimeTypes, window.MimeTypeArray.prototype);
    } catch (e) {}
    defineGetter(Navigator.prototype, 'mimeTypes', () => fakeMimeTypes);
    try {
        Navigator.prototype.getBattery = () => Promise.resolve({
            charging: true,
            chargingTime: 0,
            dischargingTime: Infinity,
            level: 1,
            addEventListener: () => {},
            removeEventListener: () => {},
            dispatchEvent: () => true,
        });
    } catch (e) {}

    if (!window.chrome) {
        try {
            Object.defineProperty(window, 'chrome', {
                value: {
                    app: { isInstalled: false, InstallState: {}, RunningState: {} },
                    runtime: {},
                    csi: () => ({ startE: Date.now(), onloadT: Date.now(), pageT: 1, tran: 15 }),
                    loadTimes: () => ({ requestTime: Date.now() / 1000, startLoadTime: Date.now() / 1000 }),
                },
                configurable: true,
            });
        } catch (e) {}
    }

    const glParams = new Map([
        [0x1F00, 'Google Inc. (Apple)'],
        [0x1F01, 'ANGLE (Apple, ANGLE Metal Renderer: Apple M1 Max, Unspecified Version)'],
        [0x1F02, 'WebGL 1.0 (OpenGL ES 2.0 Chromium)'],
        [0x8B8C, 'WebGL GLSL ES 1.0 (OpenGL ES GLSL ES 1.0 Chromium)'],
        [0x9245, 'Google Inc. (Apple)'],
        [0x9246, 'ANGLE (Apple, ANGLE Metal Renderer: Apple M1 Max, Unspecified Version)'],
    ]);
    const fakeGL = {
        getExtension: name => name === 'WEBGL_debug_renderer_info'
            ? { UNMASKED_VENDOR_WEBGL: 0x9245, UNMASKED_RENDERER_WEBGL: 0x9246 }
            : null,
        getParameter: param => glParams.has(param) ? glParams.get(param) : 0,
        getSupportedExtensions: () => ['WEBGL_debug_renderer_info', 'OES_texture_float'],
    };
    const canvasProto = window.HTMLCanvasElement && HTMLCanvasElement.prototype;
    if (canvasProto) {
        const originalGetContext = canvasProto.getContext;
        canvasProto.getContext = function(type, ...args) {
            if (/webgl/i.test(String(type))) return fakeGL;
            return originalGetContext ? originalGetContext.call(this, type, ...args) : null;
        };
        canvasProto.toDataURL = () => 'data:image/png;base64,iVBORw0KGgo=';
    }
})();"""


# ---------------------------------------------------------------------------

async def _run_ultrastealth_extraction(
    url: str,
    extract_js: str,
    wait_secs: float = 3.0,
    pre_eval_js: list[str] | None = None,
    solve_cloudflare: bool = False,
) -> dict:
    """Drive UltrastealthFetcher and return the parsed JSON result of extract_js."""
    from ultrastealth import UltrastealthFetcher

    async with UltrastealthFetcher() as us:
        return await us.fetch_and_evaluate(
            url, f"({extract_js})()",
            wait_secs=wait_secs,
            pre_eval_js=pre_eval_js,
            solve_cloudflare=solve_cloudflare,
        )


async def _run_patchright_extraction(
    url: str,
    extract_js: str,
    wait_secs: float = 3.0,
    pre_eval_js: list[str] | None = None,
    solve_cloudflare: bool = False,
) -> dict:
    """Same as _run_ultrastealth_extraction, but forces the patchright engine.

    Goes through fetcher.py's UltrastealthFetcher(engine="patchright") — same
    stealth flags/profile/Xvfb path as the "ultrastealth" method above, only
    the Playwright driver import differs. Unlike lightpanda/obscura, this is
    not a separate CDP-server process.
    """
    from ultrastealth import UltrastealthFetcher

    async with UltrastealthFetcher(engine="patchright") as us:
        return await us.fetch_and_evaluate(
            url, f"({extract_js})()",
            wait_secs=wait_secs,
            pre_eval_js=pre_eval_js,
            solve_cloudflare=solve_cloudflare,
        )


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _find_lightpanda_binary() -> str | None:
    explicit = os.environ.get("LIGHTPANDA_BINARY")
    if explicit:
        return explicit

    from_path = shutil.which("lightpanda")
    if from_path:
        return from_path

    for candidate in (
        Path.cwd() / "lightpanda",
        Path.cwd() / "tools" / "lightpanda",
    ):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def _lightpanda_user_agent() -> str:
    return os.environ.get("LIGHTPANDA_USER_AGENT") or LIGHTPANDA_DEFAULT_USER_AGENT


def _lightpanda_stealth_enabled() -> bool:
    return os.environ.get("LIGHTPANDA_STEALTH", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def _lightpanda_stealth_script() -> str:
    return _LIGHTPANDA_STEALTH_JS.replace(
        "__LIGHTPANDA_USER_AGENT__",
        json.dumps(_lightpanda_user_agent()),
    )


def _lightpanda_serve_command(binary: str, port: int) -> list[str]:
    command = [
        binary,
        "serve",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--user-agent",
        _lightpanda_user_agent(),
    ]

    extra_args = os.environ.get("LIGHTPANDA_ARGS")
    if extra_args:
        command.extend(extra_args.split())
    return command


async def _wait_for_lightpanda(endpoint: str, proc: asyncio.subprocess.Process, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    version_url = endpoint.rstrip("/") + "/json/version"
    last_error = ""

    while time.monotonic() < deadline:
        if proc.returncode is not None:
            raise RuntimeError(f"Lightpanda exited before CDP was ready (exit {proc.returncode})")
        try:
            with urlopen(version_url, timeout=0.25) as response:
                if response.status < 500:
                    return
        except (OSError, URLError) as exc:
            last_error = str(exc)
        await asyncio.sleep(0.1)

    raise TimeoutError(f"Timed out waiting for Lightpanda CDP at {endpoint}: {last_error}")


@asynccontextmanager
async def _lightpanda_endpoint():
    configured = os.environ.get("LIGHTPANDA_CDP_ENDPOINT")
    if configured:
        yield configured
        return

    binary = _find_lightpanda_binary()
    if not binary:
        raise RuntimeError(
            "Lightpanda binary not found. Set LIGHTPANDA_BINARY, put ./lightpanda "
            "or ./tools/lightpanda in this repo, or install `lightpanda` on PATH."
        )

    port = int(os.environ.get("LIGHTPANDA_PORT") or _find_free_port())
    endpoint = f"http://127.0.0.1:{port}"
    env = os.environ.copy()
    env.setdefault("LIGHTPANDA_DISABLE_TELEMETRY", "true")
    env.setdefault("LIGHTPANDA_DISABLE_CORE_DUMP", "1")
    proc = await asyncio.create_subprocess_exec(
        *_lightpanda_serve_command(binary, port),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )
    try:
        await _wait_for_lightpanda(endpoint, proc)
        yield endpoint
    finally:
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()


async def _run_lightpanda_extraction(
    url: str,
    extract_js: str,
    wait_secs: float = 3.0,
    pre_eval_js: list[str] | None = None,
    solve_cloudflare: bool = False,
) -> dict:
    """Drive Lightpanda over CDP and return the parsed JSON result of extract_js."""
    from rebrowser_playwright.async_api import async_playwright

    async with _lightpanda_endpoint() as endpoint:
        playwright = await async_playwright().start()
        browser = None
        context = None
        page = None
        try:
            browser = await playwright.chromium.connect_over_cdp(endpoint, timeout=10000)
            try:
                context = await browser.new_context()
            except Exception:
                context = browser.contexts[0] if browser.contexts else await browser.new_context()

            if _lightpanda_stealth_enabled():
                try:
                    await context.add_init_script(_lightpanda_stealth_script())
                except Exception:
                    pass

            page = await context.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(wait_secs)

            # Lightpanda has no graphical rendering and this benchmark intentionally
            # measures it without Ultrastealth's challenge solver/bypass layer.
            if solve_cloudflare:
                await asyncio.sleep(min(wait_secs, 2.0))

            if pre_eval_js:
                for expr in pre_eval_js:
                    try:
                        await page.evaluate(expr)
                    except Exception:
                        pass
                await asyncio.sleep(wait_secs)

            return await page.evaluate(f"({extract_js})()")
        finally:
            if page:
                try:
                    await page.close()
                except Exception:
                    pass
            if context:
                try:
                    await context.close()
                except Exception:
                    pass
            if browser:
                try:
                    await browser.close()
                except Exception:
                    pass
            await playwright.stop()


def _find_obscura_binary() -> str | None:
    explicit = os.environ.get("OBSCURA_BINARY")
    if explicit:
        return explicit

    from_path = shutil.which("obscura")
    if from_path:
        return from_path

    for candidate in (
        Path.cwd() / "obscura",
        Path.cwd() / "tools" / "obscura",
    ):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def _obscura_user_agent() -> str:
    return os.environ.get("OBSCURA_USER_AGENT") or OBSCURA_DEFAULT_USER_AGENT


def _obscura_stealth_enabled() -> bool:
    return os.environ.get("OBSCURA_STEALTH", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def _obscura_serve_command(binary: str, port: int) -> list[str]:
    command = [
        binary,
        "serve",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--user-agent",
        _obscura_user_agent(),
        "--quiet",
    ]

    if _obscura_stealth_enabled():
        command.append("--stealth")

    extra_args = os.environ.get("OBSCURA_ARGS")
    if extra_args:
        command.extend(extra_args.split())
    return command


async def _wait_for_obscura(endpoint: str, proc: asyncio.subprocess.Process, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    version_url = endpoint.rstrip("/") + "/json/version"
    last_error = ""

    while time.monotonic() < deadline:
        if proc.returncode is not None:
            raise RuntimeError(f"Obscura exited before CDP was ready (exit {proc.returncode})")
        try:
            with urlopen(version_url, timeout=0.25) as response:
                if response.status < 500:
                    return
        except (OSError, URLError) as exc:
            last_error = str(exc)
        await asyncio.sleep(0.1)

    raise TimeoutError(f"Timed out waiting for Obscura CDP at {endpoint}: {last_error}")


@asynccontextmanager
async def _obscura_endpoint():
    configured = os.environ.get("OBSCURA_CDP_ENDPOINT")
    if configured:
        yield configured
        return

    binary = _find_obscura_binary()
    if not binary:
        raise RuntimeError(
            "Obscura binary not found. Set OBSCURA_BINARY, put ./obscura "
            "or ./tools/obscura in this repo, or install `obscura` on PATH."
        )

    port = int(os.environ.get("OBSCURA_PORT") or _find_free_port())
    endpoint = f"http://127.0.0.1:{port}"
    proc = await asyncio.create_subprocess_exec(
        *_obscura_serve_command(binary, port),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        await _wait_for_obscura(endpoint, proc)
        yield endpoint
    finally:
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()


async def _run_obscura_extraction(
    url: str,
    extract_js: str,
    wait_secs: float = 3.0,
    pre_eval_js: list[str] | None = None,
    solve_cloudflare: bool = False,
) -> dict:
    """Drive Obscura over CDP and return the parsed JSON result of extract_js."""
    from rebrowser_playwright.async_api import async_playwright

    async with _obscura_endpoint() as endpoint:
        playwright = await async_playwright().start()
        browser = None
        context = None
        page = None
        try:
            browser = await playwright.chromium.connect_over_cdp(endpoint, timeout=10000)
            try:
                context = await browser.new_context()
            except Exception:
                context = browser.contexts[0] if browser.contexts else await browser.new_context()

            page = await context.new_page()
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(wait_secs)

            # Obscura's own --stealth mode provides the anti-detection layer here;
            # this benchmark measures it as shipped, without extra JS shims.
            if solve_cloudflare:
                await asyncio.sleep(min(wait_secs, 2.0))

            if pre_eval_js:
                for expr in pre_eval_js:
                    try:
                        await page.evaluate(expr)
                    except Exception:
                        pass
                await asyncio.sleep(wait_secs)

            return await page.evaluate(f"({extract_js})()")
        finally:
            if page:
                try:
                    await page.close()
                except Exception:
                    pass
            if context:
                try:
                    await context.close()
                except Exception:
                    pass
            if browser:
                try:
                    await browser.close()
                except Exception:
                    pass
            await playwright.stop()


async def _run_extraction(
    method: str,
    url: str,
    extract_js: str,
    wait_secs: float = 3.0,
    pre_eval_js: list[str] | None = None,
    expose_function: bool = False,
    solve_cloudflare: bool = False,
) -> dict:
    """Navigate to URL, optionally run pre-eval JS, then extract data via extract_js."""
    del expose_function  # Kept for site definitions that describe trigger behavior.

    if method == "ultrastealth":
        return await _run_ultrastealth_extraction(
            url,
            extract_js,
            wait_secs=wait_secs,
            pre_eval_js=pre_eval_js,
            solve_cloudflare=solve_cloudflare,
        )
    if method == "patchright":
        return await _run_patchright_extraction(
            url,
            extract_js,
            wait_secs=wait_secs,
            pre_eval_js=pre_eval_js,
            solve_cloudflare=solve_cloudflare,
        )
    if method == "lightpanda":
        return await _run_lightpanda_extraction(
            url,
            extract_js,
            wait_secs=wait_secs,
            pre_eval_js=pre_eval_js,
            solve_cloudflare=solve_cloudflare,
        )
    if method == "obscura":
        return await _run_obscura_extraction(
            url,
            extract_js,
            wait_secs=wait_secs,
            pre_eval_js=pre_eval_js,
            solve_cloudflare=solve_cloudflare,
        )
    raise ValueError(f"Unknown benchmark method: {method}")


# ---------------------------------------------------------------------------
# Shared scoring helpers
# ---------------------------------------------------------------------------

# Well-known reverse-proxy/CDN outage signatures. Each entry maps to the short
# status code we report; matching is case-insensitive substring search over
# whatever raw text/HTML a site's extraction JS surfaced.
_OUTAGE_SIGNATURES: tuple[tuple[str, str], ...] = (
    ("502", "502 bad gateway"),
    ("503", "503 service unavailable"),
    ("504", "504 gateway time-out"),
    ("504", "504 gateway timeout"),
    ("521", "521: web server is down"),
    ("522", "522: connection timed out"),
    ("523", "523: origin is unreachable"),
)


def _detect_outage(text: str, html: str = "") -> str | None:
    """Return a short status code (e.g. "502") if `text`/`html` look like a
    reverse-proxy/CDN gateway-error page rather than the site's real content;
    None otherwise.

    Conservative by design: only fires on well-known nginx/Cloudflare gateway
    error-page phrasing, never on an ordinary "no signal detected" page. A
    bare "nginx" banner only counts alongside one of the numeric gateway codes
    (502/503/504), since detection sites can legitimately mention unrelated
    numbers or words on their own.
    """
    haystack = f"{text}\n{html}".lower()
    if not haystack.strip():
        return None

    for code, phrase in _OUTAGE_SIGNATURES:
        if phrase in haystack:
            return code

    if "nginx" in haystack:
        for code in ("502", "503", "504"):
            if code in haystack:
                return code

    return None


# ---------------------------------------------------------------------------
# Site test implementations
# ---------------------------------------------------------------------------

_SANNYSOFT_JS = """() => {
    const out = { tests: [], meta: {} };
    document.querySelectorAll('table tr').forEach(row => {
        const cells = row.querySelectorAll('td');
        if (cells.length >= 2) {
            const name = cells[0]?.innerText?.trim();
            const val = cells[1]?.innerText?.trim();
            const cls = cells[1]?.className || '';
            if (name) out.tests.push({ name, value: val?.substring(0, 150), cls });
        }
    });
    out.meta.webdriver = navigator.webdriver;
    out.meta.webdriver_type = typeof navigator.webdriver;
    out.meta.plugins = navigator.plugins.length;
    out.meta.languages = navigator.languages;
    out.meta.chrome = !!window.chrome;
    out.meta.platform = navigator.platform;
    out.meta.userAgent = navigator.userAgent;
    try {
        const c = document.createElement('canvas');
        const gl = c.getContext('webgl');
        const dbg = gl.getExtension('WEBGL_debug_renderer_info');
        out.meta.webgl_vendor = gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL);
        out.meta.webgl_renderer = gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL);
    } catch(e) { out.meta.webgl = 'error'; }
    return out;
}"""


async def test_sannysoft(method: str) -> SiteResult:
    """bot.sannysoft.com — Classic Intoli + fingerprint scanner tests."""
    result = SiteResult(site="sannysoft", method=method)
    start = time.time()

    try:
        collected = await _run_extraction(method, "https://bot.sannysoft.com/", _SANNYSOFT_JS, wait_secs=3)
    except Exception as e:
        result.error = str(e)[:200]
        result.elapsed_ms = round((time.time() - start) * 1000)
        return result

    result.elapsed_ms = round((time.time() - start) * 1000)
    result.raw = collected.get("meta", {})

    for t in collected.get("tests", []):
        name, val, cls = t["name"], t["value"], t["cls"]
        if "passed" in cls or "passed" in val.lower() or val == "ok":
            result.tests.append(asdict(TestResult(name, True, val, "pass")))
            result.passed += 1
        elif "failed" in cls or "failed" in val.lower():
            result.tests.append(asdict(TestResult(name, False, val, "fail")))
            result.failed += 1
        elif "warn" in cls.lower() or val == "WARN":
            result.tests.append(asdict(TestResult(name, None, val, "warn")))
            result.warned += 1
        else:
            continue

    result.total = result.passed + result.failed + result.warned
    return result


_REBROWSER_EXTRACT_JS = """() => {
    const el = document.getElementById('detections-json');
    const jsonText = el ? el.innerText?.trim() : '';
    const rows = [];
    document.querySelectorAll('table tr, [class*="test"], [data-test]').forEach(row => {
        const tds = row.querySelectorAll('td');
        if (tds.length >= 2) {
            rows.push({
                icon: tds[0]?.innerText?.trim(),
                col1: tds[1]?.innerText?.trim(),
                col2: tds[2]?.innerText?.trim() || '',
                col3: tds[3]?.innerText?.trim()?.substring(0, 200) || ''
            });
        }
    });
    return { json: jsonText, rows };
}"""


async def test_rebrowser(method: str) -> SiteResult:
    """bot-detector.rebrowser.net — Modern CDP leak and automation detection."""
    result = SiteResult(site="rebrowser", method=method)
    start = time.time()

    triggers = [
        "window.dummyFn()",
        "document.getElementById('detections-json')",
        "document.getElementsByClassName('div')",
    ]

    try:
        collected = await _run_extraction(
            method, "https://bot-detector.rebrowser.net/",
            _REBROWSER_EXTRACT_JS, wait_secs=2,
            pre_eval_js=triggers, expose_function=True,
        )
    except Exception as e:
        result.error = str(e)[:200]
        result.elapsed_ms = round((time.time() - start) * 1000)
        return result

    result.elapsed_ms = round((time.time() - start) * 1000)

    if collected.get("json"):
        try:
            detections = json.loads(collected["json"])
            for det in detections:
                name = det.get("name", "?")
                passed = det.get("passed")
                rating = det.get("rating", "?")
                note = det.get("note", "")
                if passed is True:
                    result.tests.append(asdict(TestResult(name, True, f"rating:{rating}", "pass")))
                    result.passed += 1
                elif passed is False:
                    result.tests.append(asdict(TestResult(name, False, f"rating:{rating} {note[:80]}", "fail")))
                    result.failed += 1
                else:
                    result.tests.append(asdict(TestResult(name, None, f"rating:{rating}", "skip")))
                    result.skipped += 1
            result.total = result.passed + result.failed + result.skipped
            return result
        except json.JSONDecodeError:
            pass

    for row in collected.get("rows", []):
        icon = row.get("icon", "")
        name = row.get("col1", "")
        notes = row.get("col3", "") or row.get("col2", "")
        if "\U0001f7e2" in icon:
            result.tests.append(asdict(TestResult(name, True, notes, "pass")))
            result.passed += 1
        elif "\U0001f534" in icon:
            result.tests.append(asdict(TestResult(name, False, notes, "fail")))
            result.failed += 1
        elif "\u26aa" in icon:
            result.tests.append(asdict(TestResult(name, None, notes, "skip")))
            result.skipped += 1

    result.total = result.passed + result.failed + result.skipped
    return result


_CREEPJS_JS = r"""() => {
    const out = { meta: {} };
    const allText = document.body.innerText || '';
    const fpEl = document.querySelector('[class*="fingerprint"], [class*="grade"], #fingerprint-data');
    if (fpEl) out.meta.fingerprint = fpEl.innerText?.substring(0, 300);
    const headlessEl = document.querySelector('[class*="headless"]');
    if (headlessEl) out.meta.headless = headlessEl.innerText?.substring(0, 200);
    out.meta.has_headless_warning = allText.toLowerCase().includes('headless');
    out.meta.has_bot_warning = allText.toLowerCase().includes('bot');
    out.meta.has_lie_warning = allText.toLowerCase().includes('lie');
    const fpIdMatch = allText.match(/FP ID[:\s]*([a-f0-9]+)/i);
    if (fpIdMatch) out.meta.fp_id = fpIdMatch[1];
    const stealthMatch = allText.match(/stealth[:\s]*([\d.]+%?)/i);
    if (stealthMatch) out.meta.stealth_score = stealthMatch[1];
    const headlessPct = allText.match(/[Hh]eadless\s*(?:chromium)?[:\s]*([\d.]+%)/);
    if (headlessPct) out.meta.headless_pct = headlessPct[1];
    const likeHeadless = allText.match(/like\s*headless[:\s]*([\d.]+%)/i);
    if (likeHeadless) out.meta.like_headless_pct = likeHeadless[1];
    const leadingLikeHeadless = allText.match(/([\d.]+%)\s+like\s*headless/i);
    if (leadingLikeHeadless) out.meta.like_headless_pct = leadingLikeHeadless[1];
    return out;
}"""


def _parse_percent(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace("%", "")
    try:
        return float(text)
    except ValueError:
        return None


def _creepjs_checks(meta: dict) -> list[tuple[str, bool, str]]:
    like_headless = _parse_percent(meta.get("like_headless_pct") or meta.get("headless_pct"))
    if like_headless is None:
        headless_passed = not meta.get("has_headless_warning", True)
        headless_value = ""
    else:
        # CreepJS displays a "like headless" percentage even for ordinary browsers.
        # Treat it as a signal strength, not as a binary failure from the word alone.
        headless_passed = like_headless < 50
        headless_value = f"{like_headless:g}% like headless"

    return [
        ("headless_detection", headless_passed, headless_value),
        ("bot_detection", not meta.get("has_bot_warning", True), ""),
        ("lie_detection", not meta.get("has_lie_warning", True), ""),
    ]


async def test_creepjs(method: str) -> SiteResult:
    """abrahamjuliot.github.io/creepjs — Deep fingerprint analysis."""
    result = SiteResult(site="creepjs", method=method)
    start = time.time()

    try:
        collected = await _run_extraction(method, "https://abrahamjuliot.github.io/creepjs/", _CREEPJS_JS, wait_secs=8)
    except Exception as e:
        result.error = str(e)[:200]
        result.elapsed_ms = round((time.time() - start) * 1000)
        return result

    result.elapsed_ms = round((time.time() - start) * 1000)
    result.raw = collected.get("meta", {})

    for name, passed, value in _creepjs_checks(result.raw):
        result.tests.append(asdict(TestResult(name, passed, value, "pass" if passed else "fail")))
        if passed:
            result.passed += 1
        else:
            result.failed += 1
    result.total = result.passed + result.failed
    return result


_INFOSIMPLES_JS = r"""() => {
    const out = { tests: [] };
    document.querySelectorAll('tr, .test-result, li').forEach(el => {
        const text = el.innerText?.trim();
        if (!text || text.length > 300) return;
        const isPass = text.includes('\u2713') || text.includes('PASS') || text.includes('pass')
            || el.classList?.contains('pass') || el.querySelector('.pass, .text-success');
        const isFail = text.includes('\u2717') || text.includes('FAIL') || text.includes('fail')
            || el.classList?.contains('fail') || el.querySelector('.fail, .text-danger');
        if (isPass || isFail) {
            out.tests.push({
                name: text.replace(/[\u2713\u2717]/g, '').trim().substring(0, 80),
                passed: isPass && !isFail
            });
        }
    });
    if (out.tests.length === 0) {
        // infosimples.github.io/detect-headless renders one <tr> per named probe
        // (scripts/detect_headless.js) with a fixed value string per test, e.g.
        // "Detected 5 plugins" -- that word "Detected" is a HEALTHY reading (a real
        // browser reports non-zero plugins/mimeTypes/languages) and must NOT be
        // treated as a generic bad-word substring. Score each named probe using the
        // page's own pass/fail/undefined semantics instead of a keyword blocklist.
        document.querySelectorAll('table tr').forEach(row => {
            const cells = row.querySelectorAll('td, th');
            if (cells.length < 2) return;
            const name = cells[0]?.innerText?.trim();
            const val = cells[1]?.innerText?.trim();
            if (!name || !val || name === 'Test') return;  // 'Test'/'Result' header row

            const lower = val.toLowerCase();
            let passed = null;  // null = inconclusive/undefined -> reported as skip
            let m;

            switch (name) {
                case 'Plugins':
                    m = val.match(/Detected (\d+) plugins/i);
                    passed = m ? (parseInt(m[1], 10) > 0 ? true : null) : null;
                    break;
                case 'Mime':
                    m = val.match(/Detected (\d+) mime types/i);
                    passed = m ? (parseInt(m[1], 10) > 0 ? true : null) : null;
                    break;
                case 'Languages':
                    m = val.match(/Detected (\d+) languages/i);
                    passed = !!m && parseInt(m[1], 10) > 0 && !/using\s*$/i.test(val);
                    break;
                case 'Webdriver':
                    passed = /missing webdriver/i.test(val);
                    break;
                case 'Plugins Prototype':
                case 'Mime Prototype':
                    passed = /are consistent/i.test(val) && !/aren't consistent/i.test(val);
                    break;
                case 'Chrome':
                    passed = /not present/i.test(val) ? null : true;
                    break;
                case 'Devtool Protocol':
                    passed = /not using/i.test(val) ? true : null;
                    break;
                case 'Connection Rtt':
                    if (/not defined/i.test(val)) { passed = null; break; }
                    m = val.match(/Connection-rtt:\s*(\d+)/i);
                    passed = !!m && parseInt(m[1], 10) !== 0;
                    break;
                case 'Time Elapse':
                    m = val.match(/Time elapsed to close alert:\s*(\d+)/i);
                    passed = !!m && parseInt(m[1], 10) >= 30;
                    break;
                case 'Broken Image':
                    m = val.match(/width (\d+) and height (\d+)/i);
                    passed = m ? !(m[1] === '0' && m[2] === '0') : true;
                    break;
                case 'Outer dimensions':
                    m = val.match(/Outerheight:\s*(\d+) and outerwidth:\s*(\d+)/i);
                    passed = m ? !(m[1] === '0' && m[2] === '0') : true;
                    break;
                case 'Permission':
                    if (/undefined/i.test(val)) { passed = null; break; }
                    passed = !(/"denied"/i.test(val) && /"prompt"/i.test(val));
                    break;
                case 'Mouse Move':
                    passed = /move your mouse/i.test(val) ? null : /vary in mouse events/i.test(val);
                    break;
                case 'User Agent':
                case 'App Version':
                    passed = !lower.includes('headless');
                    break;
                default:
                    // Unrecognized row: only flag explicit failure wording, never the
                    // bare word "detected" (a healthy reading for several checks above).
                    passed = !lower.includes('headless') && !lower.includes('fail');
            }

            const entry = { name: name.substring(0, 80), value: val.substring(0, 100), passed };
            if (passed === null) entry.severity = 'skip';
            out.tests.push(entry);
        });
    }
    return out;
}"""


async def test_infosimples(method: str) -> SiteResult:
    """infosimples.github.io/detect-headless — Headless Chrome detection."""
    result = SiteResult(site="infosimples", method=method)
    start = time.time()

    try:
        collected = await _run_extraction(method, "https://infosimples.github.io/detect-headless/", _INFOSIMPLES_JS, wait_secs=3)
    except Exception as e:
        result.error = str(e)[:200]
        result.elapsed_ms = round((time.time() - start) * 1000)
        return result

    result.elapsed_ms = round((time.time() - start) * 1000)
    return _append_collected_tests(result, collected)


_AREYOUHEADLESS_JS = """() => {
    const text = document.body.innerText || '';
    const notHeadless = text.includes('You are not Chrome headless')
        || text.includes('not chrome headless');
    const isHeadless = !notHeadless && (
        text.includes('You are Chrome headless')
        || text.includes('you are chrome headless')
    );
    return {
        full_text: text.substring(0, 500),
        detected_as_headless: isHeadless,
        detected_as_not_headless: notHeadless
    };
}"""


async def test_areyouheadless(method: str) -> SiteResult:
    """arh.antoinevastel.com — Advanced headless detection."""
    result = SiteResult(site="areyouheadless", method=method)
    start = time.time()

    try:
        collected = await _run_extraction(method, "https://arh.antoinevastel.com/bots/areyouheadless", _AREYOUHEADLESS_JS, wait_secs=3)
    except Exception as e:
        result.error = str(e)[:200]
        result.elapsed_ms = round((time.time() - start) * 1000)
        return result

    result.elapsed_ms = round((time.time() - start) * 1000)
    full_text = collected.get("full_text", "")
    result.raw = {"response": full_text[:300]}

    outage = _detect_outage(full_text)
    if outage:
        result.error = f"site unreachable ({outage})"
        return result

    passed = collected.get("detected_as_not_headless", False)
    result.tests.append(asdict(TestResult(
        "headless_detection", passed,
        full_text[:100],
        "pass" if passed else "fail"
    )))
    if passed:
        result.passed += 1
    else:
        result.failed += 1
    result.total = 1
    return result


_BROWSERSCAN_JS = r"""() => {
    const out = { tests: [], meta: {} };
    const allText = document.body.innerText || '';
    const seen = new Set();
    document.querySelectorAll('[class*="item"], [class*="result"], [class*="check"], [class*="card"], tr').forEach(el => {
        const text = el.innerText?.trim();
        if (!text || text.length > 250 || text.length < 5) return;
        if (seen.has(text)) return;
        const hasNormal = /\bnormal\b/i.test(text);
        const hasAbnormal = /\babnormal\b/i.test(text);
        if (hasNormal || hasAbnormal) {
            seen.add(text);
            const lines = text.split('\n');
            const name = lines[0]?.substring(0, 50) || text.substring(0, 50);
            out.tests.push({ name, value: hasNormal && !hasAbnormal ? 'Normal' : 'Abnormal', passed: hasNormal && !hasAbnormal });
        }
    });
    const checks = [
        { name: 'WebDriver', pattern: /webdriver[:\s]*(true|false|detected|not detected)/i },
        { name: 'CDP', pattern: /cdp[:\s]*(detected|not detected|true|false)/i },
        { name: 'Automation', pattern: /automation[:\s]*(detected|not detected|true|false)/i },
    ];
    for (const check of checks) {
        const m = allText.match(check.pattern);
        if (m && !out.tests.some(t => t.name === check.name)) {
            const val = m[1].toLowerCase();
            const passed = val === 'false' || val === 'not detected';
            out.tests.push({ name: check.name, value: m[1], passed });
        }
    }
    out.meta.webdriver = navigator.webdriver;
    return out;
}"""


async def test_browserscan(method: str) -> SiteResult:
    """browserscan.net/bot-detection — WebDriver, CDP, 50+ attributes."""
    result = SiteResult(site="browserscan", method=method)
    start = time.time()

    try:
        collected = await _run_extraction(method, "https://www.browserscan.net/bot-detection", _BROWSERSCAN_JS, wait_secs=6)
    except Exception as e:
        result.error = str(e)[:200]
        result.elapsed_ms = round((time.time() - start) * 1000)
        return result

    result.elapsed_ms = round((time.time() - start) * 1000)
    result.raw = {k: v for k, v in collected.get("meta", {}).items() if k != "full_text_snippet"}

    for t in collected.get("tests", []):
        name = t.get("name", "?")
        passed = t.get("passed", False)
        result.tests.append(asdict(TestResult(name, passed, t.get("value", ""), "pass" if passed else "fail")))
        if passed:
            result.passed += 1
        else:
            result.failed += 1
    result.total = result.passed + result.failed
    return result


_INCOLUMITAS_JS = r"""() => {
    const out = { tests: [], meta: {} };
    const allText = document.body.innerText || '';
    const resultsEl = document.querySelector('#detection-results, #results, [id*="result"], pre');
    if (resultsEl) {
        try { out.meta.json_results = JSON.parse(resultsEl.innerText); }
        catch(e) { out.meta.results_text = resultsEl.innerText?.substring(0, 500); }
    }
    document.querySelectorAll('.bot-test, [class*="test-row"], [class*="detection"]').forEach(el => {
        const name = el.querySelector('.test-name, .name, strong, b')?.innerText?.trim();
        const resultEl = el.querySelector('.test-result, .result, .value');
        const resultText = resultEl?.innerText?.trim() || '';
        const isPass = el.classList?.contains('passed') || resultText.includes('human') || resultText.includes('pass');
        const isFail = el.classList?.contains('failed') || resultText.includes('bot') || resultText.includes('fail');
        if (name && (isPass || isFail)) {
            out.tests.push({ name: name.substring(0, 80), value: resultText.substring(0, 100), passed: isPass && !isFail });
        }
    });
    if (out.tests.length === 0) {
        const wdMatch = allText.match(/webdriver[:\s]*(true|false|detected|not detected)/i);
        if (wdMatch) {
            const wd_passed = wdMatch[1].toLowerCase() === 'false' || wdMatch[1].toLowerCase() === 'not detected';
            out.tests.push({ name: 'webdriver', value: wdMatch[1], passed: wd_passed });
        }
        const behScore = allText.match(/behavioral.*?score[:\s]*([\d.]+)/i);
        if (behScore) {
            const score = parseFloat(behScore[1]);
            out.meta.behavioral_score = score;
            if (!isNaN(score)) out.tests.push({ name: 'behavioral_score', value: String(score), passed: score > 0.5 });
        }
    }
    out.meta.detected_bot = allText.toLowerCase().includes('bot detected') || allText.toLowerCase().includes('automation detected');
    out.meta.webdriver = navigator.webdriver;
    return out;
}"""


async def test_incolumitas(method: str) -> SiteResult:
    """bot.incolumitas.com — Constantly updated Puppeteer/Playwright detection."""
    result = SiteResult(site="incolumitas", method=method)
    start = time.time()

    try:
        collected = await _run_extraction(method, "https://bot.incolumitas.com/", _INCOLUMITAS_JS, wait_secs=6)
    except Exception as e:
        result.error = str(e)[:200]
        result.elapsed_ms = round((time.time() - start) * 1000)
        return result

    result.elapsed_ms = round((time.time() - start) * 1000)
    result.raw = {k: v for k, v in collected.get("meta", {}).items() if k != "full_text"}

    for t in collected.get("tests", []):
        name = t.get("name", "?")
        passed = t.get("passed", False)
        result.tests.append(asdict(TestResult(name, passed, t.get("value", ""), "pass" if passed else "fail")))
        if passed:
            result.passed += 1
        else:
            result.failed += 1
    result.total = result.passed + result.failed
    return result


_PIXELSCAN_JS = r"""() => {
    const out = { tests: [], meta: {} };
    const allText = document.body.innerText || '';
    const seen = new Set();
    document.querySelectorAll('[class*="row"], [class*="check"], [class*="item"], [class*="card"], div, span').forEach(el => {
        const text = el.innerText?.trim();
        if (!text || text.length > 200 || text.length < 5) return;
        if (seen.has(text)) return;
        const hasConsistent = /\bconsistent\b/i.test(text) && !/\binconsistent\b/i.test(text);
        const hasInconsistent = /\binconsistent\b/i.test(text);
        if (hasConsistent || hasInconsistent) {
            seen.add(text);
            const lines = text.split('\n');
            const name = lines[0]?.substring(0, 50) || text.substring(0, 50);
            out.tests.push({ name, value: hasConsistent ? 'Consistent' : 'Inconsistent', passed: hasConsistent });
        }
    });
    if (allText.match(/webdriver[:\s]*(true|detected)/i)) {
        out.tests.push({ name: 'WebDriver', value: 'detected', passed: false });
    }
    out.meta.detected_bot = allText.includes('Bot Detected') || allText.includes('Automation');
    out.meta.consistent_count = out.tests.filter(t => t.passed).length;
    out.meta.inconsistent_count = out.tests.filter(t => !t.passed).length;
    out.meta.webdriver = navigator.webdriver;
    return out;
}"""


async def test_pixelscan(method: str) -> SiteResult:
    """pixelscan.net — Comprehensive fingerprint consistency check."""
    result = SiteResult(site="pixelscan", method=method)
    start = time.time()

    try:
        collected = await _run_extraction(method, "https://pixelscan.net/", _PIXELSCAN_JS, wait_secs=8)
    except Exception as e:
        result.error = str(e)[:200]
        result.elapsed_ms = round((time.time() - start) * 1000)
        return result

    result.elapsed_ms = round((time.time() - start) * 1000)
    result.raw = collected.get("meta", {})

    for t in collected.get("tests", []):
        name = t.get("name", "?")
        passed = t.get("passed", False)
        result.tests.append(asdict(TestResult(name, passed, t.get("value", ""), "pass" if passed else "fail")))
        if passed:
            result.passed += 1
        else:
            result.failed += 1
    result.total = result.passed + result.failed
    return result


# ---------------------------------------------------------------------------
# TLS/fingerprint sites (tested via browser for comparison with HTTP benchmark)
# ---------------------------------------------------------------------------

_PEETWS_JS = """() => {
    try { return JSON.parse(document.body.innerText || document.querySelector('pre')?.innerText || '{}'); }
    catch(e) { return {}; }
}"""


async def test_peetws(method: str) -> SiteResult:
    """tls.peet.ws — JA3/JA4/HTTP2 fingerprint via browser."""
    result = SiteResult(site="peetws", method=method)
    start = time.time()

    try:
        data = await _run_extraction(method, "https://tls.peet.ws/api/all", _PEETWS_JS, wait_secs=3)
    except Exception as e:
        result.error = str(e)[:200]
        result.elapsed_ms = round((time.time() - start) * 1000)
        return result

    result.elapsed_ms = round((time.time() - start) * 1000)
    tls = data.get("tls", {})
    ja4 = tls.get("ja4", "")
    http_ver = data.get("http_version", "")

    result.raw = {
        "ja4": ja4[:40],
        "http_version": http_ver,
        "user_agent": data.get("user_agent", "")[:80],
    }

    # HTTP/2
    is_h2 = "2" in http_ver
    result.tests.append(asdict(TestResult("HTTP/2", is_h2, http_ver, "pass" if is_h2 else "fail")))

    # JA4 Chrome-like
    ja4_ok = ja4.startswith("t13d") if ja4 else False
    result.tests.append(asdict(TestResult("JA4 Chrome-like", ja4_ok, ja4[:30], "pass" if ja4_ok else "fail")))

    # Chrome UA
    ua = data.get("user_agent", "")
    ua_ok = "Chrome/" in ua and "HeadlessChrome" not in ua
    result.tests.append(asdict(TestResult("Chrome UA (not headless)", ua_ok, ua[:60], "pass" if ua_ok else "fail")))

    for t in result.tests:
        if t["severity"] == "pass":
            result.passed += 1
        elif t["severity"] == "fail":
            result.failed += 1
    result.total = result.passed + result.failed
    return result


_BROWSERLEAKS_JS = """() => {
    try { return JSON.parse(document.body.innerText || document.querySelector('pre')?.innerText || '{}'); }
    catch(e) { return {}; }
}"""


async def test_browserleaks(method: str) -> SiteResult:
    """tls.browserleaks.com — JA3/JA4/Akamai fingerprint via browser."""
    result = SiteResult(site="browserleaks", method=method)
    start = time.time()

    try:
        data = await _run_extraction(method, "https://tls.browserleaks.com/json", _BROWSERLEAKS_JS, wait_secs=3)
    except Exception as e:
        result.error = str(e)[:200]
        result.elapsed_ms = round((time.time() - start) * 1000)
        return result

    result.elapsed_ms = round((time.time() - start) * 1000)
    ja4 = data.get("ja4", "")
    akamai = data.get("akamai_hash", "")

    result.raw = {
        "ja4": ja4[:40],
        "akamai_hash": akamai[:32],
        "user_agent": data.get("user_agent", "")[:80],
    }

    # JA4 Chrome-like
    ja4_ok = ja4.startswith("t13d") if ja4 else False
    result.tests.append(asdict(TestResult("JA4 Chrome-like", ja4_ok, ja4[:30], "pass" if ja4_ok else "fail")))

    # Akamai H2 fingerprint present
    result.tests.append(asdict(TestResult("Akamai H2 FP", bool(akamai), akamai[:32] if akamai else "empty",
                                          "pass" if akamai else "fail")))

    for t in result.tests:
        if t["severity"] == "pass":
            result.passed += 1
        elif t["severity"] == "fail":
            result.failed += 1
    result.total = result.passed + result.failed
    return result


_CLOUDFLARE_JS = """() => {
    const text = document.body.innerText || '';
    const html = document.documentElement.outerHTML || '';
    // "has_content" = we got past the CF gate and onto the real site.
    // Use HTML length as fallback because some target pages (e.g. nowsecure.nl)
    // render most of their content via CSS animations with tiny innerText.
    return {
        has_challenge: text.includes('Checking your browser') || text.includes('Turnstile') ||
                       text.includes('hCaptcha') || html.includes('<title>Just a moment'),
        has_content: text.length > 500 || html.length > 10000,
        title: document.title,
        text_snippet: text.substring(0, 300),
        html_length: html.length,
    };
}"""


async def test_cloudflare(method: str) -> SiteResult:
    """nowsecure.nl — Cloudflare JS challenge bypass via browser."""
    result = SiteResult(site="cloudflare", method=method)
    start = time.time()

    try:
        data = await _run_extraction(method, "https://nowsecure.nl/", _CLOUDFLARE_JS, wait_secs=8,
                                     solve_cloudflare=True)
    except Exception as e:
        result.error = str(e)[:200]
        result.elapsed_ms = round((time.time() - start) * 1000)
        return result

    result.elapsed_ms = round((time.time() - start) * 1000)
    result.raw = {
        "title": data.get("title", ""),
        "has_content": data.get("has_content", False),
    }

    outage = _detect_outage(data.get("text_snippet", ""))
    if outage:
        result.error = f"site unreachable ({outage})"
        return result

    bypassed = data.get("has_content", False) and not data.get("has_challenge", True)
    result.tests.append(asdict(TestResult("CF JS Challenge Bypass", bypassed,
                                          "passed" if bypassed else "blocked",
                                          "pass" if bypassed else "fail")))

    for t in result.tests:
        if t["severity"] == "pass":
            result.passed += 1
        elif t["severity"] == "fail":
            result.failed += 1
    result.total = result.passed + result.failed
    return result


_CFTRACE_JS = """() => {
    const text = document.body.innerText || document.querySelector('pre')?.innerText || '';
    const result = {};
    text.split('\\n').forEach(line => {
        if (line.includes('=')) {
            const [k, ...v] = line.split('=');
            result[k.trim()] = v.join('=').trim();
        }
    });
    return result;
}"""


async def test_cftrace(method: str) -> SiteResult:
    """cloudflare.com/cdn-cgi/trace — TLS/HTTP version via browser."""
    result = SiteResult(site="cftrace", method=method)
    start = time.time()

    try:
        data = await _run_extraction(method, "https://www.cloudflare.com/cdn-cgi/trace", _CFTRACE_JS, wait_secs=2)
    except Exception as e:
        result.error = str(e)[:200]
        result.elapsed_ms = round((time.time() - start) * 1000)
        return result

    result.elapsed_ms = round((time.time() - start) * 1000)
    result.raw = {
        "tls": data.get("tls", ""),
        "http": data.get("http", ""),
        "user_agent": data.get("uag", "")[:80],
    }

    # HTTP/2+
    http = data.get("http", "")
    is_h2 = "http/2" in http or "http/3" in http or "h3" in http
    result.tests.append(asdict(TestResult("HTTP/2+", is_h2, http, "pass" if is_h2 else "fail")))

    # Chrome UA (not headless)
    ua = data.get("uag", "")
    ua_ok = "Chrome/" in ua and "HeadlessChrome" not in ua
    result.tests.append(asdict(TestResult("Chrome UA (not headless)", ua_ok, ua[:60], "pass" if ua_ok else "fail")))

    for t in result.tests:
        if t["severity"] == "pass":
            result.passed += 1
        elif t["severity"] == "fail":
            result.failed += 1
        elif t["severity"] == "warn":
            result.warned += 1
    result.total = result.passed + result.failed + result.warned
    return result


_DEVICEBROWSERINFO_JS = r"""() => {
    const out = { tests: [], meta: {} };
    let jsonText = '';
    document.querySelectorAll('pre').forEach(el => {
        const t = (el.innerText || '').trim();
        if (!jsonText && t.startsWith('{') && t.includes('isBot')) jsonText = t;
    });
    let parsed = null;
    try { parsed = JSON.parse(jsonText); } catch (e) { out.meta.parse_error = true; }
    if (parsed) {
        out.tests.push({ name: 'isBot', value: String(parsed.isBot), passed: parsed.isBot === false });
        const d = parsed.details || {};
        for (const k in d) {
            // each detail boolean true = a bot signal detected = fail
            out.tests.push({ name: k, value: String(d[k]), passed: d[k] === false });
        }
    }
    const card = document.querySelector('#resultsBotTest');
    out.meta.cardClass = card ? card.className : null;
    return out;
}"""


async def test_deviceandbrowserinfo(method: str) -> SiteResult:
    """deviceandbrowserinfo.com/are_you_a_bot — Vastel fingerprint-only bot test (~20 checks)."""
    result = SiteResult(site="devbrowserinfo", method=method)
    start = time.time()

    try:
        collected = await _run_extraction(method, "https://deviceandbrowserinfo.com/are_you_a_bot", _DEVICEBROWSERINFO_JS, wait_secs=3)
    except Exception as e:
        result.error = str(e)[:200]
        result.elapsed_ms = round((time.time() - start) * 1000)
        return result

    result.elapsed_ms = round((time.time() - start) * 1000)
    result.raw = {"cardClass": collected.get("meta", {}).get("cardClass")}

    for t in collected.get("tests", []):
        name = t.get("name", "?")
        passed = t.get("passed", False)
        result.tests.append(asdict(TestResult(name, passed, t.get("value", ""), "pass" if passed else "fail")))
        if passed:
            result.passed += 1
        else:
            result.failed += 1
    result.total = result.passed + result.failed
    return result


_IPHEY_JS = r"""() => {
    const out = { tests: [], meta: {} };
    // iphey renders 4 category tiles (.code-block.<name>-tile); a flagged one carries
    // the class `code-block--error`. Score each tile by absence of that error class.
    // (location-tile is IP/geo-bound; browser/hardware/software are fingerprint signals.)
    document.querySelectorAll('[class*="-tile"]').forEach(el => {
        const cls = (el.className || '').toString();
        const m = cls.match(/(browser|location|hardware|software)-tile/);
        if (!m) return;
        const errored = /code-block--error/.test(cls);
        const fine = /everything is fine/i.test(el.innerText || '');
        out.tests.push({ name: m[1], value: errored ? 'flagged' : (fine ? 'fine' : 'ok'), passed: !errored });
    });
    const hero = document.querySelector('[class*="hero-status--"]');
    if (hero) out.meta.overall = (hero.innerText || '').trim();   // IP-influenced; informational only
    if (!out.tests.length) out.meta.no_tiles = true;
    return out;
}"""


async def test_iphey(method: str) -> SiteResult:
    """iphey.com — digital-identity verdict (note: overall is IP/proxy-influenced, not fingerprint-only)."""
    result = SiteResult(site="iphey", method=method)
    start = time.time()

    try:
        collected = await _run_extraction(method, "https://iphey.com/", _IPHEY_JS, wait_secs=6)
    except Exception as e:
        result.error = str(e)[:200]
        result.elapsed_ms = round((time.time() - start) * 1000)
        return result

    result.elapsed_ms = round((time.time() - start) * 1000)
    result.raw = collected.get("meta", {})

    for t in collected.get("tests", []):
        name = t.get("name", "?")
        passed = t.get("passed", False)
        result.tests.append(asdict(TestResult(name, passed, t.get("value", ""), "pass" if passed else "fail")))
        if passed:
            result.passed += 1
        else:
            result.failed += 1
    result.total = result.passed + result.failed
    return result


_FINGERPRINTSCAN_JS = r"""() => {
    const out = { tests: [], meta: {} };
    const scoreEl = document.querySelector('#fingerprintScore');
    const text = scoreEl ? (scoreEl.innerText || '') : (document.body.innerText || '');
    const m = text.match(/Bot Risk Score:\s*(\d+)\s*\/\s*100/i);
    if (m) {
        const score = parseInt(m[1], 10);
        out.meta.bot_risk_score = score;
        // page rule: a score above 50 means you are most likely a bot
        out.tests.push({ name: 'bot_risk_score', value: score + '/100', passed: score < 50 });
    } else {
        out.meta.no_score = true;
    }
    return out;
}"""


async def test_fingerprintscan(method: str) -> SiteResult:
    """fingerprint-scan.com — FPScanner bot-risk score (0-100; <50 = human)."""
    result = SiteResult(site="fingerprintscan", method=method)
    start = time.time()

    try:
        collected = await _run_extraction(method, "https://fingerprint-scan.com/", _FINGERPRINTSCAN_JS, wait_secs=4)
    except Exception as e:
        result.error = str(e)[:200]
        result.elapsed_ms = round((time.time() - start) * 1000)
        return result

    result.elapsed_ms = round((time.time() - start) * 1000)
    result.raw = collected.get("meta", {})

    for t in collected.get("tests", []):
        name = t.get("name", "?")
        passed = t.get("passed", False)
        result.tests.append(asdict(TestResult(name, passed, t.get("value", ""), "pass" if passed else "fail")))
        if passed:
            result.passed += 1
        else:
            result.failed += 1
    result.total = result.passed + result.failed
    return result


def _append_collected_tests(result: SiteResult, collected: dict) -> SiteResult:
    """Apply a standard `{tests, meta}` extraction payload to a SiteResult."""
    result.raw = collected.get("meta", {})
    for t in collected.get("tests", []):
        name = t.get("name", "?")
        passed = t.get("passed")
        value = t.get("value", "")
        if passed is True:
            severity = "pass"
            result.passed += 1
        elif passed is False:
            severity = "fail"
            result.failed += 1
        else:
            severity = t.get("severity", "warn")
            if severity == "skip":
                result.skipped += 1
            else:
                severity = "warn"
                result.warned += 1
        result.tests.append(asdict(TestResult(name, passed, value, severity)))

    result.total = result.passed + result.failed + result.warned + result.skipped
    return result


_SELENIUM_DETECTOR_PRE_JS = r"""(async () => {
    try {
        const token = window.token || '';
        const asyncToken = typeof window.getAsyncToken === 'function'
            ? await window.getAsyncToken()
            : '';
        const tokenInput = document.querySelector('#chromedriver-token');
        const asyncInput = document.querySelector('#chromedriver-asynctoken');
        if (tokenInput) {
            tokenInput.value = token;
            tokenInput.dispatchEvent(new Event('input', { bubbles: true }));
        }
        if (asyncInput) {
            asyncInput.value = asyncToken;
            asyncInput.dispatchEvent(new Event('input', { bubbles: true }));
        }
        document.querySelector('#chromedriver-test')?.click();
    } catch (e) {}
})()"""


_SELENIUM_DETECTOR_JS = r"""() => {
    const out = { tests: [], meta: {} };
    const text = document.body.innerText || '';
    const add = (name, passed, value = '') => out.tests.push({ name, passed, value: String(value).substring(0, 160) });

    add('navigator.webdriver', navigator.webdriver !== true, navigator.webdriver);

    const seleniumGlobals = Object.getOwnPropertyNames(window).filter(k =>
        /webdriver|selenium|chromedriver|cdc_/i.test(k)
    );
    add('selenium_globals_absent', seleniumGlobals.length === 0, seleniumGlobals.join(', '));

    const pageStatus = (text.match(/Chromedriver Detector\s+(Passed|Error|Failed)!?/i) || [])[0] || '';
    const resultText = pageStatus || Array.from(document.querySelectorAll(
        '#chromedriver-result, #result, .result, [id*="result"], [class*="result"], pre'
    )).map(el => (el.innerText || '').trim()).find(Boolean) || '';
    if (resultText) {
        const normalized = resultText.toLowerCase();
        const clean = /not detected|passed|success|undetected|not found/.test(normalized);
        const detected = /\b(detected|failed|bot)\b/.test(normalized) && !/not detected/.test(normalized);
        add('page_verdict', clean && !detected, resultText);
    } else {
        add('page_verdict', false, 'no verdict found');
    }

    out.meta.title = document.title;
    out.meta.has_token = typeof window.token !== 'undefined';
    out.meta.has_async_token = typeof window.getAsyncToken === 'function';
    out.meta.body_snippet = text.substring(0, 240);
    return out;
}"""


def _normalize_seleniumdetector_payload(collected: dict) -> dict:
    """Prefer Selenium Detector's page-level headline over noisy form labels."""
    body = str(collected.get("meta", {}).get("body_snippet", "")).lower()
    if "chromedriver detector passed" not in body:
        return collected

    tests = collected.setdefault("tests", [])
    for test in tests:
        if test.get("name") == "page_verdict":
            test["passed"] = True
            test["value"] = "Chromedriver Detector Passed"
            return collected

    tests.append({
        "name": "page_verdict",
        "passed": True,
        "value": "Chromedriver Detector Passed",
    })
    return collected


async def test_seleniumdetector(method: str) -> SiteResult:
    """hmaker.github.io/selenium-detector — ChromeDriver/Selenium token probe."""
    result = SiteResult(site="seleniumdetector", method=method)
    start = time.time()

    try:
        collected = await _run_extraction(
            method,
            "https://hmaker.github.io/selenium-detector/",
            _SELENIUM_DETECTOR_JS,
            wait_secs=2,
            pre_eval_js=[_SELENIUM_DETECTOR_PRE_JS],
        )
    except Exception as e:
        result.error = str(e)[:200]
        result.elapsed_ms = round((time.time() - start) * 1000)
        return result

    result.elapsed_ms = round((time.time() - start) * 1000)
    return _append_collected_tests(result, _normalize_seleniumdetector_payload(collected))


_BROTECTOR_JS = r"""() => {
    const out = { tests: [], meta: {} };
    const text = document.body.innerText || '';
    const seen = new Set();
    const add = (name, passed, value = '') => {
        const key = `${name}:${value}`;
        if (seen.has(key)) return;
        seen.add(key);
        out.tests.push({ name, passed, value: String(value).substring(0, 160) });
    };

    document.querySelectorAll('tr, li, [class*="test"], [class*="result"], [data-testid], div').forEach(el => {
        const rowText = (el.innerText || '').trim();
        if (!rowText || rowText.length < 4 || rowText.length > 240) return;
        const normalized = rowText.toLowerCase();
        const mentionsProbe = /coordinates|istrusted|popup|pwinit|webdriver|selenium|playwright|chromedriver|detected|passed|failed/.test(normalized);
        if (!mentionsProbe) return;
        const hasGood = /not detected|passed|pass|clean|ok|false/.test(normalized);
        const hasBad = /detected|failed|fail|leak|crash|true/.test(normalized) && !/not detected/.test(normalized);
        if (hasGood || hasBad) {
            const name = rowText.split('\n')[0].substring(0, 80);
            add(name, hasGood && !hasBad, rowText);
        }
    });

    add('navigator.webdriver', navigator.webdriver !== true, navigator.webdriver);
    out.meta.title = document.title;
    out.meta.body_snippet = text.substring(0, 240);
    return out;
}"""


async def test_brotector(method: str) -> SiteResult:
    """ttlns.github.io/brotector — browser automation and event leak detector."""
    result = SiteResult(site="brotector", method=method)
    start = time.time()

    try:
        collected = await _run_extraction(
            method,
            "https://ttlns.github.io/brotector/",
            _BROTECTOR_JS,
            wait_secs=5,
        )
    except Exception as e:
        result.error = str(e)[:200]
        result.elapsed_ms = round((time.time() - start) * 1000)
        return result

    result.elapsed_ms = round((time.time() - start) * 1000)
    return _append_collected_tests(result, collected)


_RECAPTCHA_DEMO_JS = r"""() => {
    const out = { tests: [], meta: {} };
    const text = document.body.innerText || '';
    const html = document.documentElement.outerHTML || '';
    const frames = Array.from(document.querySelectorAll('iframe')).map(frame => frame.src || '');
    const hasRecaptcha = frames.some(src => /recaptcha/i.test(src)) ||
        !!document.querySelector('.g-recaptcha, [data-sitekey], textarea[name="g-recaptcha-response"], input[name="g-recaptcha-response"]');
    const response = document.querySelector('textarea[name="g-recaptcha-response"], input[name="g-recaptcha-response"]')?.value || '';
    const blocked = /access denied|just a moment|checking your browser|unusual traffic|service unavailable/i.test(text);

    out.tests.push({ name: 'page_loaded', passed: text.length > 20 || html.length > 1000, value: document.title });
    out.tests.push({ name: 'recaptcha_rendered', passed: hasRecaptcha, value: frames.find(src => /recaptcha/i.test(src)) || '' });
    out.tests.push({ name: 'no_block_page', passed: !blocked, value: blocked ? text.substring(0, 120) : 'ok' });
    if (response) {
        out.tests.push({ name: 'recaptcha_response_present', passed: response.length > 20, value: `${response.length} chars` });
    }

    out.meta.title = document.title;
    out.meta.frame_count = frames.length;
    out.meta.recaptcha_frames = frames.filter(src => /recaptcha/i.test(src)).length;
    out.meta.response_length = response.length;
    return out;
}"""


async def test_recaptcha_v2_invisible(method: str) -> SiteResult:
    """2captcha.com demo — reCAPTCHA v2 invisible rendering/access probe."""
    result = SiteResult(site="recaptcha_v2_invisible", method=method)
    start = time.time()

    try:
        collected = await _run_extraction(
            method,
            "https://2captcha.com/demo/recaptcha-v2-invisible",
            _RECAPTCHA_DEMO_JS,
            wait_secs=5,
        )
    except Exception as e:
        result.error = str(e)[:200]
        result.elapsed_ms = round((time.time() - start) * 1000)
        return result

    result.elapsed_ms = round((time.time() - start) * 1000)
    return _append_collected_tests(result, collected)


async def test_recaptcha_v3(method: str) -> SiteResult:
    """2captcha.com demo — reCAPTCHA v3 rendering/access probe."""
    result = SiteResult(site="recaptcha_v3", method=method)
    start = time.time()

    try:
        collected = await _run_extraction(
            method,
            "https://2captcha.com/demo/recaptcha-v3",
            _RECAPTCHA_DEMO_JS,
            wait_secs=5,
        )
    except Exception as e:
        result.error = str(e)[:200]
        result.elapsed_ms = round((time.time() - start) * 1000)
        return result

    result.elapsed_ms = round((time.time() - start) * 1000)
    return _append_collected_tests(result, collected)


_TURNSTILE_DEMO_JS = r"""() => {
    const out = { tests: [], meta: {} };
    const text = document.body.innerText || '';
    const html = document.documentElement.outerHTML || '';
    const frames = Array.from(document.querySelectorAll('iframe')).map(frame => frame.src || '');
    const hasTurnstile = frames.some(src => /challenges\.cloudflare\.com|turnstile/i.test(src)) ||
        !!document.querySelector('.cf-turnstile, [data-sitekey], input[name="cf-turnstile-response"], textarea[name="cf-turnstile-response"]');
    const response = document.querySelector('input[name="cf-turnstile-response"], textarea[name="cf-turnstile-response"]')?.value || '';
    const blocked = /access denied|just a moment|checking your browser|service unavailable/i.test(text);

    out.tests.push({ name: 'page_loaded', passed: text.length > 20 || html.length > 1000, value: document.title });
    out.tests.push({ name: 'turnstile_rendered', passed: hasTurnstile, value: frames.find(src => /cloudflare|turnstile/i.test(src)) || '' });
    out.tests.push({ name: 'no_block_page', passed: !blocked, value: blocked ? text.substring(0, 120) : 'ok' });
    out.tests.push({ name: 'turnstile_response_present', passed: response.length > 20, value: `${response.length} chars` });

    out.meta.title = document.title;
    out.meta.frame_count = frames.length;
    out.meta.turnstile_frames = frames.filter(src => /cloudflare|turnstile/i.test(src)).length;
    out.meta.response_length = response.length;
    return out;
}"""


async def test_turnstiledemo(method: str) -> SiteResult:
    """turnstiledemo.lusostreams.com — embedded Cloudflare Turnstile demo."""
    result = SiteResult(site="turnstiledemo", method=method)
    start = time.time()

    try:
        collected = await _run_extraction(
            method,
            "https://turnstiledemo.lusostreams.com/",
            _TURNSTILE_DEMO_JS,
            wait_secs=8,
            solve_cloudflare=True,
        )
    except Exception as e:
        result.error = str(e)[:200]
        result.elapsed_ms = round((time.time() - start) * 1000)
        return result

    result.elapsed_ms = round((time.time() - start) * 1000)
    return _append_collected_tests(result, collected)


_EGP_ANNOUNCEMENTS_JS = r"""() => {
    const out = { tests: [], meta: {} };
    const text = document.body.innerText || '';
    const html = document.documentElement.outerHTML || '';
    const title = document.title || '';
    const hasChallenge = /challenges\.cloudflare\.com|cf-turnstile|just a moment|checking your browser/i.test(html + '\n' + text);
    const hasAnnouncementShell = /announcement|egp-agpc|ประกาศ|จัดซื้อ|จัดจ้าง|กรมบัญชีกลาง/i.test(text + '\n' + html);
    const hardError = /access denied|service unavailable|not authorized|forbidden/i.test(text);

    out.tests.push({ name: 'page_loaded', passed: html.length > 1000, value: title });
    out.tests.push({ name: 'no_cloudflare_challenge', passed: !hasChallenge, value: hasChallenge ? 'challenge present' : 'ok' });
    out.tests.push({ name: 'announcement_app_visible', passed: hasAnnouncementShell && !hardError, value: text.substring(0, 160) });

    out.meta.title = title;
    out.meta.html_length = html.length;
    out.meta.text_length = text.length;
    out.meta.has_challenge = hasChallenge;
    return out;
}"""


async def test_egp_announcements(method: str) -> SiteResult:
    """process5.gprocurement.go.th — Thai e-GP announcement access probe."""
    result = SiteResult(site="egp_announcements", method=method)
    start = time.time()

    try:
        collected = await _run_extraction(
            method,
            "https://process5.gprocurement.go.th/egp-agpc01-web/announcement?announcementTodayFlag=true",
            _EGP_ANNOUNCEMENTS_JS,
            wait_secs=10,
            solve_cloudflare=True,
        )
    except Exception as e:
        result.error = str(e)[:200]
        result.elapsed_ms = round((time.time() - start) * 1000)
        return result

    result.elapsed_ms = round((time.time() - start) * 1000)
    return _append_collected_tests(result, collected)


# ---------------------------------------------------------------------------
# Site registry
# ---------------------------------------------------------------------------

SITES = {
    "sannysoft": {"fn": test_sannysoft, "url": "bot.sannysoft.com", "desc": "Intoli + fingerprint tests"},
    "rebrowser": {"fn": test_rebrowser, "url": "bot-detector.rebrowser.net", "desc": "CDP leak / automation detection"},
    "creepjs": {"fn": test_creepjs, "url": "abrahamjuliot.github.io/creepjs", "desc": "Deep fingerprint analysis"},
    "infosimples": {"fn": test_infosimples, "url": "infosimples.github.io/detect-headless", "desc": "Headless Chrome detection"},
    "areyouheadless": {"fn": test_areyouheadless, "url": "arh.antoinevastel.com", "desc": "Advanced headless detection"},
    "browserscan": {"fn": test_browserscan, "url": "browserscan.net/bot-detection", "desc": "WebDriver + CDP + 50 attributes"},
    "devbrowserinfo": {"fn": test_deviceandbrowserinfo, "url": "deviceandbrowserinfo.com/are_you_a_bot", "desc": "Vastel fingerprint-only bot test (~20 checks)"},
    "iphey": {"fn": test_iphey, "url": "iphey.com", "desc": "Digital-identity verdict (IP/proxy-influenced)"},
    "fingerprintscan": {"fn": test_fingerprintscan, "url": "fingerprint-scan.com", "desc": "FPScanner bot-risk score (<50=human)"},
    "incolumitas": {"fn": test_incolumitas, "url": "bot.incolumitas.com", "desc": "PW/Puppeteer detection (updated)"},
    "pixelscan": {"fn": test_pixelscan, "url": "pixelscan.net", "desc": "Fingerprint consistency check"},
    "peetws": {"fn": test_peetws, "url": "tls.peet.ws/api/all", "desc": "JA3/JA4/HTTP2 fingerprint"},
    "browserleaks": {"fn": test_browserleaks, "url": "tls.browserleaks.com/json", "desc": "JA3/JA4/Akamai fingerprint"},
    "cloudflare": {"fn": test_cloudflare, "url": "nowsecure.nl", "desc": "Cloudflare JS challenge bypass"},
    "cftrace": {"fn": test_cftrace, "url": "cloudflare.com/cdn-cgi/trace", "desc": "Cloudflare TLS/HTTP trace"},
    "brotector": {"fn": test_brotector, "url": "ttlns.github.io/brotector", "desc": "Browser automation/event leak detector"},
    "seleniumdetector": {"fn": test_seleniumdetector, "url": "hmaker.github.io/selenium-detector", "desc": "ChromeDriver/Selenium token detector"},
    "recaptcha_v2_invisible": {"fn": test_recaptcha_v2_invisible, "url": "2captcha.com/demo/recaptcha-v2-invisible", "desc": "reCAPTCHA v2 invisible demo access"},
    "recaptcha_v3": {"fn": test_recaptcha_v3, "url": "2captcha.com/demo/recaptcha-v3", "desc": "reCAPTCHA v3 demo access"},
    "turnstiledemo": {"fn": test_turnstiledemo, "url": "turnstiledemo.lusostreams.com", "desc": "Cloudflare Turnstile demo access"},
    "egp_announcements": {"fn": test_egp_announcements, "url": "process5.gprocurement.go.th announcement", "desc": "Thai e-GP announcement access"},
}

# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

def _aggregate_for_json(results: list[dict]) -> dict:
    by_method: dict[str, dict] = {}
    for r in results:
        m, site = r["method"], r["site"]
        bm = by_method.setdefault(m, {"_p": 0, "_scored": 0, "sites": {}})
        passed, failed = r.get("passed", 0), r.get("failed", 0)
        # Rate is over passed+failed only; skipped/warned tests never fired a
        # real probe (e.g. main-world-only checks under alwaysIsolated) and
        # must not dilute the pass rate as if they were failures.
        scored = passed + failed
        bm["_p"] += passed
        bm["_scored"] += scored
        bm["sites"][site] = (passed / scored) if scored else 0.0
    return {m: {"rate": (bm["_p"] / bm["_scored"]) if bm["_scored"] else 0.0, "sites": bm["sites"]}
            for m, bm in by_method.items()}


def print_table(results: list[dict], show_details: bool = False):
    print()
    print("=" * 94)
    print("  BOT DETECTION BENCHMARK RESULTS")
    print("=" * 94)

    by_site: dict[str, list[dict]] = {}
    for r in results:
        by_site.setdefault(r["site"], []).append(r)

    header = f"{'Site':<18} {'Method':<14} {'Pass':>5} {'Fail':>5} {'Skip':>5} {'Score':>8} {'Rate':>7} {'Time':>8}"
    print()
    print(header)
    print("-" * len(header))

    for site_name in SITES:
        site_results = by_site.get(site_name, [])
        for r in site_results:
            if r.get("error"):
                print(f"{r['site']:<18} {r['method']:<14} {'':>5} {'':>5} {'':>5} {'ERROR':>8} {'':>7} {r['elapsed_ms']:>7}ms")
            else:
                passed = r.get("passed", 0)
                failed = r.get("failed", 0)
                skipped = r.get("skipped", 0) + r.get("warned", 0)
                # Rate/score are over passed+failed only: skipped/warned tests
                # (e.g. main-world-only probes under alwaysIsolated) never fired
                # and must not dilute the pass rate as if they were failures.
                scored = passed + failed
                rate = f"{passed/scored*100:.0f}%" if scored > 0 else "N/A"
                score = f"{passed}/{scored}" if scored > 0 else "N/A"
                print(f"{r['site']:<18} {r['method']:<14} {passed:>5} {failed:>5} {skipped:>5} {score:>8} {rate:>7} {r['elapsed_ms']:>7}ms")

    print()
    print("=" * 94)
    print("  METHOD COMPARISON (aggregated)")
    print("=" * 94)

    by_method: dict[str, dict] = {}
    for r in results:
        m = r["method"]
        if m not in by_method:
            by_method[m] = {"total_pass": 0, "total_fail": 0, "total_skip": 0, "total_tests": 0, "sites_ok": 0, "sites_err": 0, "total_ms": 0}
        if r.get("error"):
            by_method[m]["sites_err"] += 1
        else:
            by_method[m]["total_pass"] += r.get("passed", 0)
            by_method[m]["total_fail"] += r.get("failed", 0)
            by_method[m]["total_skip"] += r.get("skipped", 0) + r.get("warned", 0)
            by_method[m]["total_tests"] += r.get("total", 0)
            by_method[m]["sites_ok"] += 1
        by_method[m]["total_ms"] += r.get("elapsed_ms", 0)

    header2 = f"{'Method':<14} {'Description':<45} {'Pass':>5} {'Fail':>5} {'Rate':>7} {'Sites':>6} {'Time':>9}"
    print()
    print(header2)
    print("-" * len(header2))
    for m, agg in by_method.items():
        desc = METHODS.get(m, m)[:45]
        # Rate is over passed+failed only; skipped/warned probes never fired
        # and must not dilute it (see per-site rate above for the same fix).
        scored = agg["total_pass"] + agg["total_fail"]
        rate = f"{agg['total_pass']/scored*100:.0f}%" if scored > 0 else "N/A"
        sites_str = f"{agg['sites_ok']}/{agg['sites_ok'] + agg['sites_err']}"
        print(f"{m:<14} {desc:<45} {agg['total_pass']:>5} {agg['total_fail']:>5} {rate:>7} {sites_str:>6} {agg['total_ms']:>8}ms")

    print()
    best = max(
        by_method.items(),
        key=lambda x: x[1]["total_pass"] / max(x[1]["total_pass"] + x[1]["total_fail"], 1),
    )
    print(f"  >>> Best stealth: {best[0]} ({METHODS.get(best[0], best[0])})")
    print()

    if show_details:
        print("=" * 94)
        print("  FAILED TEST DETAILS")
        print("=" * 94)
        for r in results:
            fails = [t for t in r.get("tests", []) if t.get("severity") == "fail"]
            if fails:
                print(f"\n  {r['site']} / {r['method']}:")
                for t in fails:
                    val = t.get("value", "")
                    print(f"    FAIL: {t['name']}" + (f" -- {val[:80]}" if val else ""))

        print()
        print("=" * 94)
        print("  RAW METADATA")
        print("=" * 94)
        for r in results:
            if r.get("raw"):
                print(f"\n  {r['site']} / {r['method']}:")
                for k, v in r["raw"].items():
                    print(f"    {k}: {v}")


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

async def run_benchmark(site_names: list[str], method_names: list[str]) -> list[dict]:
    results = []
    total = len(site_names) * len(method_names)
    idx = 0

    for site_name in site_names:
        site = SITES[site_name]
        for method_name in method_names:
            idx += 1
            label = f"[{idx}/{total}]"
            print(f"\n{label} Testing {site_name} ({site['url']}) with {method_name}...", flush=True)

            try:
                sr = await site["fn"](method_name)
                results.append(asdict(sr))
                if sr.error:
                    print(f"  ERROR: {sr.error[:100]}")
                else:
                    # Score is over passed+failed only; skipped/warned probes
                    # never fired and are reported separately, not folded in.
                    scored = sr.passed + sr.failed
                    extra = sr.skipped + sr.warned
                    suffix = f", {extra} skip" if extra else ""
                    print(f"  Score: {sr.passed}/{scored} pass, {sr.failed} fail{suffix}, {sr.elapsed_ms}ms")
            except Exception as e:
                print(f"  EXCEPTION: {e}")
                traceback.print_exc()
                results.append(asdict(SiteResult(site=site_name, method=method_name, error=str(e)[:200])))

    return results


def _rebrowser_patch_status() -> bool | None:
    """Best-effort patch_rebrowser.is_patched() check; None if unavailable
    (module missing, import error, etc.) rather than failing the run."""
    try:
        try:
            from . import patch_rebrowser
        except ImportError:
            import patch_rebrowser
        return patch_rebrowser.is_patched()
    except Exception:
        return None


def _chrome_binary_version() -> str:
    """Best-effort resolved Chrome/Chromium `--version` string; "" on any
    failure (binary not found, exec error, timeout, ...) rather than failing
    the run."""
    try:
        try:
            from . import fetcher as _fetcher
        except ImportError:
            import fetcher as _fetcher
        binary = _fetcher._find_chrome()
        if not binary:
            return ""
        proc = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=5)
        return (proc.stdout or proc.stderr or "").strip()
    except Exception:
        return ""


def main():
    parser = argparse.ArgumentParser(description="Bot Detection Benchmark Suite")
    parser.add_argument("--sites", nargs="*", default=list(SITES.keys()), choices=list(SITES.keys()))
    parser.add_argument("--methods", nargs="*", default=list(METHODS.keys()), choices=list(METHODS.keys()))
    parser.add_argument("--results", default="bot_benchmark_results.json")
    parser.add_argument("--compare", metavar="FILE", help="Print table from previous results file")
    parser.add_argument("--details", action="store_true", help="Show failed test details")
    parser.add_argument("--list", action="store_true", help="List available sites and methods")
    parser.add_argument("--json", action="store_true", help="Print machine-readable per-method aggregate as the final stdout line")
    args = parser.parse_args()

    if args.list:
        print("Available sites:")
        for k, v in SITES.items():
            print(f"  {k:<18} {v['url']:<40} {v['desc']}")
        print("\nAvailable methods:")
        for k, v in METHODS.items():
            print(f"  {k:<14} {v}")
        return

    if args.compare:
        with open(args.compare) as f:
            data = json.load(f)
        print_table(data["results"], show_details=args.details)
        return

    print("Bot Detection Benchmark Suite")
    print(f"Date: {datetime.now(timezone.utc).isoformat()}")
    print(f"REBROWSER_PATCHES: {os.environ.get('REBROWSER_PATCHES_RUNTIME_FIX_MODE', 'not set')}")
    print(f"Sites: {', '.join(args.sites)}")
    print(f"Methods: {', '.join(args.methods)}")
    print(f"Combinations: {len(args.sites) * len(args.methods)}")

    results = asyncio.run(run_benchmark(args.sites, args.methods))

    output = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "env": {
            "rebrowser_mode": os.environ.get("REBROWSER_PATCHES_RUNTIME_FIX_MODE", "not set"),
            "python": sys.version,
            "rebrowser_patched": _rebrowser_patch_status(),
            "chrome_version": _chrome_binary_version(),
        },
        "sites_tested": args.sites,
        "methods_tested": args.methods,
        "results": results,
    }

    with open(args.results, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\nResults saved to {args.results}")

    print_table(results, show_details=args.details)

    if args.json:
        print(json.dumps(_aggregate_for_json(results)))


if __name__ == "__main__":
    main()
