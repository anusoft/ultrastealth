"""Neutralize rebrowser_playwright driver fingerprints (idempotent, revertible).

Some bot detectors (e.g. bot-detector.rebrowser.net) probe the page context for
identifiers that Playwright's bundled Node driver leaks:

  * ``globalThis.__pwInitScripts`` — the init-script dedup map created by every
    ``addInitScript`` (page.js). A JS bypass cannot reliably hide it: the driver
    re-creates it *before* user scripts run, so it must be renamed at the source.
  * ``UtilityScript`` — the class wrapping every ``page.evaluate`` call. Its name
    shows up in ``Error().stack`` captured by page JS during an evaluate.
  * ``globalThis.__playwright_builtins__`` — a cache of native ``setTimeout``/
    ``Date``/``Map``/etc. that every injected script (utility script, clock,
    console API shim, WebSocket mock, the injected DOM script) creates via
    ``builtins(global)`` on first use. Still open upstream as of this writing
    (rebrowser/rebrowser-patches#110); not covered by any JS bypass since, like
    ``__pwInitScripts``, the driver recreates it before user scripts run.
  * ``globalThis.__playwright__binding__`` — the CDP ``Runtime.addBinding``
    channel name the driver exposes on every page (``PageBinding.kPlaywrightBinding``
    in ``lib/server/page.js``, mirrored by the BiDi driver's binding channel in
    ``lib/server/bidi/bidiPage.js``). A global-property sweep
    (``Object.getOwnPropertyNames(globalThis)``) finds it unconditionally, since
    the driver calls ``Runtime.addBinding`` on every frame session regardless of
    whether the caller ever uses ``exposeBinding``/``exposeFunction``. A JS
    bypass (``delete window.__playwright__binding__``) races the CDP binding
    call and can only win *after* the property already existed for one tick, so
    like the other two, this must be renamed at the source.

These live in the *installed pip package's* bundled driver, which a
``pip install -U rebrowser-playwright`` overwrites — so re-run this after any
(re)install. The renames are consistent across the driver, so functionality is
preserved (the export key, its reference, and the class declaration all move
together).

Design goals (so it survives upstream updates without merge conflicts):
  * **idempotent**  — running repeatedly is a no-op once applied.
  * **revertible**  — ``--revert`` restores the original identifiers exactly.
  * **upstream-safe** — each edit anchors on the *original* token. If a target
    file no longer contains it (and isn't already patched), the script WARNS and
    skips that file instead of corrupting it, making upstream drift visible.

Usage::

    python -m ultrastealth.patch_rebrowser            # apply
    python -m ultrastealth.patch_rebrowser --check     # report status only
    python -m ultrastealth.patch_rebrowser --revert    # undo

The replacement identifiers can be overridden via env (handy if a future
detector starts probing the defaults)::

    ULTRASTEALTH_PW_INIT_NAME=__execGuards
    ULTRASTEALTH_UTILITY_NAME=ExecutionProxy
    ULTRASTEALTH_BUILTINS_NAME=__nativeRefs
    ULTRASTEALTH_BINDING_NAME=__execChannel
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

# Replacement identifiers. Defaults are ordinary-looking JS names that blend in
# and are not what the known detectors grep for. Override via env if needed.
PW_INIT_NAME = os.environ.get("ULTRASTEALTH_PW_INIT_NAME", "__execGuards")
UTILITY_NAME = os.environ.get("ULTRASTEALTH_UTILITY_NAME", "ExecutionProxy")
BUILTINS_NAME = os.environ.get("ULTRASTEALTH_BUILTINS_NAME", "__nativeRefs")
BINDING_NAME = os.environ.get("ULTRASTEALTH_BINDING_NAME", "__execChannel")

# Sanity: replacement tokens must be valid JS identifiers and must differ from
# the originals so the idempotency check is unambiguous.
_IDENT = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")


@dataclass(frozen=True)
class Rename:
    """One identifier rename applied to one driver file."""

    rel_path: str          # path under the driver package root
    original: str          # token as upstream ships it
    replacement: str       # our neutral token
    word_boundary: bool    # match whole-word only (avoids substrings like _evaluateExposeUtilityScript)
    expected: int          # exact number of occurrences we expect upstream

    def _pattern(self, token: str) -> re.Pattern[str]:
        body = re.escape(token)
        return re.compile(rf"\b{body}\b" if self.word_boundary else body)


def _driver_root() -> Path:
    import rebrowser_playwright  # noqa: PLC0415 — optional dep, import lazily

    root = Path(rebrowser_playwright.__file__).parent / "driver" / "package"
    if not root.is_dir():
        raise FileNotFoundError(f"rebrowser driver package not found at {root}")
    return root


def _renames() -> list[Rename]:
    return [
        # __pwInitScripts: 4 uses inside the InitScript dedup template in page.js
        # (line `x = x || {}` references it twice, plus the read and the write).
        Rename("lib/server/page.js", "__pwInitScripts", PW_INIT_NAME, False, 4),
        # UtilityScript: the class wrapping evaluate. Whole-word so the unrelated
        # client method `_evaluateExposeUtilityScript` (a substring) is untouched.
        #   javascript.js: module.exports.UtilityScript() + _setPreview("UtilityScript")
        Rename("lib/server/javascript.js", "UtilityScript", UTILITY_NAME, True, 2),
        #   utilityScriptSource.js (inside the bundled source string):
        #   export key, export arrow value, and `var UtilityScript = class`.
        Rename("lib/generated/utilityScriptSource.js", "UtilityScript", UTILITY_NAME, True, 3),
        # __playwright_builtins__: each injected-script bundle embeds its own copy
        # of builtins(global) (packages/playwright-core/src/utils/isomorphic/
        # builtins.ts), which does `if (!global["__playwright_builtins__"]) {...
        # Object.defineProperty(global, "__playwright_builtins__", ...) ... return
        # global["__playwright_builtins__"]` — 3 string-literal occurrences per
        # file. Word-boundary matching handles the quoted-string usage fine since
        # `_` is a word char and the quotes/brackets around it are not. Only the
        # files actually injected into the automated page are patched here — the
        # vite-bundled trace-viewer/recorder dev-tool assets also reference this
        # token but never execute in the target page, so touching them would add
        # risk (minified bundle corruption) for zero stealth benefit.
        Rename("lib/utils/isomorphic/builtins.js", "__playwright_builtins__", BUILTINS_NAME, True, 3),
        Rename("lib/generated/utilityScriptSource.js", "__playwright_builtins__", BUILTINS_NAME, True, 3),
        Rename("lib/generated/injectedScriptSource.js", "__playwright_builtins__", BUILTINS_NAME, True, 3),
        Rename("lib/generated/clockSource.js", "__playwright_builtins__", BUILTINS_NAME, True, 3),
        Rename("lib/generated/consoleApiSource.js", "__playwright_builtins__", BUILTINS_NAME, True, 3),
        Rename("lib/generated/webSocketMockSource.js", "__playwright_builtins__", BUILTINS_NAME, True, 3),
        # __playwright__binding__: the CDP Runtime.addBinding channel name
        # (PageBinding.kPlaywrightBinding), sent on every frame session
        # regardless of exposeBinding usage — page.js defines the literal
        # once and every call site (crPage.js, wkPage.js, ffBrowser.js)
        # references it via the exported constant, so a single source-level
        # rename covers Chromium/WebKit/Firefox alike. The BiDi driver keeps
        # its own copy of the same literal as its exposed binding channel
        # name and is patched too — neither file is a vite-bundled dev-tool
        # asset (unlike the trace-viewer/recorder bundles excluded above),
        # so both are live server code and in scope.
        Rename("lib/server/page.js", "__playwright__binding__", BINDING_NAME, True, 1),
        Rename("lib/server/bidi/bidiPage.js", "__playwright__binding__", BINDING_NAME, True, 1),
    ]


def _status(text: str, r: Rename) -> str:
    """Return 'original' | 'patched' | 'drifted' for a file's content."""
    has_orig = bool(r._pattern(r.original).search(text))
    has_repl = bool(r._pattern(r.replacement).search(text))
    if has_orig and not has_repl:
        return "original"
    if has_repl and not has_orig:
        return "patched"
    if not has_orig and not has_repl:
        return "drifted"     # upstream renamed/removed the token — do not touch
    return "mixed"           # both present — unexpected; treat as drifted/unsafe


def _validate_tokens() -> None:
    for name, val, orig in (
        ("ULTRASTEALTH_PW_INIT_NAME", PW_INIT_NAME, "__pwInitScripts"),
        ("ULTRASTEALTH_UTILITY_NAME", UTILITY_NAME, "UtilityScript"),
        ("ULTRASTEALTH_BUILTINS_NAME", BUILTINS_NAME, "__playwright_builtins__"),
        ("ULTRASTEALTH_BINDING_NAME", BINDING_NAME, "__playwright__binding__"),
    ):
        if not _IDENT.match(val):
            sys.exit(f"ERROR: {name}={val!r} is not a valid JS identifier")
        if val == orig:
            sys.exit(f"ERROR: {name} must differ from the original token {orig!r}")


def run(mode: str = "apply") -> int:
    """Apply / check / revert the driver patches. Returns a process exit code."""
    _validate_tokens()
    try:
        root = _driver_root()
    except (ImportError, FileNotFoundError) as e:
        print(f"ERROR: {e}")
        return 2

    changed = warned = 0
    for r in _renames():
        path = root / r.rel_path
        if not path.is_file():
            print(f"WARN  {r.rel_path}: file missing (upstream layout changed) — skipped")
            warned += 1
            continue

        text = path.read_text(encoding="utf-8")
        state = _status(text, r)

        # Pick source/target tokens for the requested direction.
        if mode == "revert":
            src, dst, src_state = r.replacement, r.original, "patched"
        else:  # apply / check
            src, dst, src_state = r.original, r.replacement, "original"

        if mode == "check":
            n = len(r._pattern(r.original).findall(text)) + len(r._pattern(r.replacement).findall(text))
            print(f"{state:8} {r.rel_path}: {r.original} <-> {r.replacement} ({n} occurrences)")
            continue

        if state == "drifted" or state == "mixed":
            print(f"WARN  {r.rel_path}: expected token {src!r} not in a clean state "
                  f"(status={state}) — upstream may have changed; skipped")
            warned += 1
            continue

        if state != src_state:
            # Already in the target state for this direction → nothing to do.
            print(f"ok    {r.rel_path}: already {'reverted' if mode == 'revert' else 'patched'}")
            continue

        pattern = r._pattern(src)
        count = len(pattern.findall(text))
        if count != r.expected:
            print(f"WARN  {r.rel_path}: found {count} of {r.original!r}, expected {r.expected} "
                  f"— upstream drift; skipped to avoid corruption")
            warned += 1
            continue

        path.write_text(pattern.sub(dst, text), encoding="utf-8")
        print(f"PATCH {r.rel_path}: {src} -> {dst} ({count}x)")
        changed += 1

    verb = {"apply": "patched", "revert": "reverted", "check": "checked"}[mode]
    print(f"\nDone: {changed} file(s) {verb}, {warned} warning(s).")
    if warned:
        print("Some files were skipped — inspect the warnings above before relying on stealth.")
    return 1 if warned else 0


def is_patched() -> bool:
    """True iff every target file is in the patched state (for callers/health checks)."""
    try:
        root = _driver_root()
    except Exception:
        return False
    for r in _renames():
        path = root / r.rel_path
        if not path.is_file() or _status(path.read_text(encoding="utf-8"), r) != "patched":
            return False
    return True


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    mode = "apply"
    if "--check" in argv:
        mode = "check"
    elif "--revert" in argv:
        mode = "revert"
    return run(mode)


if __name__ == "__main__":
    raise SystemExit(main())
