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
import json
import os
import sys
import time
import traceback
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any


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
    "ultrastealth": "Ultrastealth (rebrowser + Xvfb + enhanced bypasses)",
}


# ---------------------------------------------------------------------------

async def _run_extraction(
    method: str,
    url: str,
    extract_js: str,
    wait_secs: float = 3.0,
    pre_eval_js: list[str] | None = None,
    expose_function: bool = False,
    solve_cloudflare: bool = False,
) -> dict:
    """Navigate to URL, optionally run pre-eval JS, then extract data via extract_js.

    Drives UltrastealthFetcher and returns the parsed JSON result of extract_js.
    (`expose_function` is honored by the fetcher's pre_eval handling.)
    """
    from ultrastealth import UltrastealthFetcher
    async with UltrastealthFetcher() as us:
        return await us.fetch_and_evaluate(
            url, f"({extract_js})()",
            wait_secs=wait_secs,
            pre_eval_js=pre_eval_js,
            solve_cloudflare=solve_cloudflare,
        )


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
    return out;
}"""


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

    checks = [
        ("headless_detection", not result.raw.get("has_headless_warning", True)),
        ("bot_detection", not result.raw.get("has_bot_warning", True)),
        ("lie_detection", not result.raw.get("has_lie_warning", True)),
    ]
    for name, passed in checks:
        result.tests.append(asdict(TestResult(name, passed, "", "pass" if passed else "fail")))
        if passed:
            result.passed += 1
        else:
            result.failed += 1
    result.total = result.passed + result.failed
    return result


_INFOSIMPLES_JS = """() => {
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
        document.querySelectorAll('table tr').forEach(row => {
            const cells = row.querySelectorAll('td, th');
            if (cells.length >= 2) {
                const name = cells[0]?.innerText?.trim();
                const val = cells[1]?.innerText?.trim();
                if (name && val) {
                    const passed = !val.toLowerCase().includes('headless') &&
                                  !val.toLowerCase().includes('detected') &&
                                  !val.toLowerCase().includes('fail');
                    out.tests.push({ name: name.substring(0, 80), value: val.substring(0, 100), passed });
                }
            }
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
    for t in collected.get("tests", []):
        name = t.get("name", "?")
        passed = t.get("passed", False)
        val = t.get("value", "")
        result.tests.append(asdict(TestResult(name, passed, val, "pass" if passed else "fail")))
        if passed:
            result.passed += 1
        else:
            result.failed += 1
    result.total = result.passed + result.failed
    return result


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
    result.raw = {"response": collected.get("full_text", "")[:300]}

    passed = collected.get("detected_as_not_headless", False)
    result.tests.append(asdict(TestResult(
        "headless_detection", passed,
        collected.get("full_text", "")[:100],
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
}

# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

def _aggregate_for_json(results: list[dict]) -> dict:
    by_method: dict[str, dict] = {}
    for r in results:
        m, site = r["method"], r["site"]
        bm = by_method.setdefault(m, {"_p": 0, "_t": 0, "sites": {}})
        passed, total = r.get("passed", 0), r.get("total", 0)
        bm["_p"] += passed
        bm["_t"] += total
        bm["sites"][site] = (passed / total) if total else 0.0
    return {m: {"rate": (bm["_p"] / bm["_t"]) if bm["_t"] else 0.0, "sites": bm["sites"]}
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
                total = r.get("total", 0)
                passed = r.get("passed", 0)
                failed = r.get("failed", 0)
                skipped = r.get("skipped", 0) + r.get("warned", 0)
                rate = f"{passed/total*100:.0f}%" if total > 0 else "N/A"
                score = f"{passed}/{total}" if total > 0 else "N/A"
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
        total = agg["total_tests"]
        rate = f"{agg['total_pass']/total*100:.0f}%" if total > 0 else "N/A"
        sites_str = f"{agg['sites_ok']}/{agg['sites_ok'] + agg['sites_err']}"
        print(f"{m:<14} {desc:<45} {agg['total_pass']:>5} {agg['total_fail']:>5} {rate:>7} {sites_str:>6} {agg['total_ms']:>8}ms")

    print()
    best = max(by_method.items(), key=lambda x: x[1]["total_pass"] / max(x[1]["total_tests"], 1))
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
                    print(f"  Score: {sr.passed}/{sr.total} pass, {sr.failed} fail, {sr.elapsed_ms}ms")
            except Exception as e:
                print(f"  EXCEPTION: {e}")
                traceback.print_exc()
                results.append(asdict(SiteResult(site=site_name, method=method_name, error=str(e)[:200])))

    return results


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
