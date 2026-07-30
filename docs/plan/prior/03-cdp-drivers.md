# Prior art: driverless / raw-CDP automation

> Thesis: dropping the Playwright Node driver removes a whole class of page-context artifacts
> **by construction** — no `__pwInitScripts`, no `UtilityScript`, no `__playwright_builtins__`,
> no forced `Runtime.enable`. You pay for it with the loss of auto-waiting, a mature selector
> engine, and reliability. For ultrastealth the honest answer is: *steal the input synthesis,
> not the driver.*

## Comparison at a glance

| Project | Stars | Last commit | Version | Transport | Needs injected JS? | Input synthesis | Selector / snapshot model | License |
|---|---|---|---|---|---|---|---|---|
| [nodriver](https://github.com/ultrafunkamsterdam/nodriver) | 4.6k | 2026-05-13 | 0.50.3 | WebSocket to `--remote-debugging-port` (discovered via `http://host:port/json`) | **No** — zero `.js` assets in the package | `Input.dispatchMouseEvent` + `dispatchKeyEvent`, linear steps, no timing model | `DOM.getDocument(-1, pierce)` walk; `backendNodeId`-keyed `Element` objects | **AGPL-3.0** |
| [zendriver](https://github.com/cdpdriver/zendriver) | 1.4k | 2026-07-15 | 0.15.5 | same | **No** (one optional `attachShadow` hook in `expert` mode) | Full `KeyEvents` layer (`core/keys.py`, 595 lines): keyDown/keyUp pairs, modifiers, graphemes/emoji | same as nodriver + nested-iframe traversal | **AGPL-3.0** |
| [pydoll](https://github.com/autoscrape-labs/pydoll) | 7.0k | 2026-07-24 | 2.23.1 | WebSocket, address resolved from `http://localhost:{port}/json/version` (`pydoll/utils/general.py:118`) | **No** | Bézier + Fitts's Law + minimum-jerk + tremor + overshoot (`pydoll/interactions/mouse.py`), typo-simulating keyboard (`interactions/keyboard.py`) | `Runtime.RemoteObjectId`-keyed `WebElement`; CSS/XPath auto-detect; typed `extractor/` model layer | **MIT** |
| ultrastealth (baseline) | — | — | — | `--remote-debugging-pipe` (fd 3/4) — *no TCP port*, confirmed at `rebrowser_playwright/.../server/chromium/chromium.js:253` | **Yes** — driver injects utility/injected/clock/console/WS-mock bundles | Playwright `Input.*` (trusted, no humanization) | `accessibility.snapshot()` → stable `eN` refs (`browser_core.py:98-132`) | MIT |

## nodriver

**Architecture.** `Browser.start()` picks a free port (`core/browser.py:317`), spawns Chrome with
`--remote-debugging-port`, then polls `http://host:port/json/version` via `HTTPApi`
(`core/browser.py:823-845`) for the browser websocket; `core/connection.py:232` opens a plain
`websockets.connect(..., max_size=2**28)`. Every CDP domain is a generated Python module under
`nodriver/cdp/` (~60 files). A `Tab` *is* a `Connection` with its own session; `Element` wraps a
`cdp.dom.Node` keyed by `backendNodeId`.

**Stealth-relevant choices.** Only 12 default flags (`core/config.py:116-129`) versus
ultrastealth's ~74 (`fetcher.py:425-505`), overlapping on just `--no-first-run`,
`--no-service-autorun`, `--no-default-browser-check`, `--no-pings`, `--disable-infobars`,
`--disable-dev-shm-usage`, `--disable-search-engine-choice-screen`. Notably **absent**:
`--disable-blink-features=AutomationControlled` — unnecessary, because it never passes
`--enable-automation` (Playwright does; ultrastealth strips it via `HARMFUL_FLAGS`,
`fetcher.py:508-514`). It *adds* two ultrastealth omits: `--remote-allow-origins=*` and
`--disable-features=IsolateOrigins,site-per-process` — both weaken the browser, and the latter
changes OOPIF behavior, so it is plausibly detectable.

`Tab.evaluate` (`core/tab.py:873-909`) is the crux: a bare `Runtime.evaluate` with
`user_gesture=True`, `allow_unsafe_eval_blocked_by_csp=True`, deep serialization. **No wrapper
function, no utility script, no isolated world.** Page JS grabbing `Error().stack` during an
evaluate sees nothing driver-shaped — compare ultrastealth, which must rename `UtilityScript` on
disk (`patch_rebrowser.py:96-99`) precisely because it *does* appear in stacks.

**Grep-confirmed:** nodriver never sends `Runtime.enable` on its own (`Page.enable` at
`core/tab.py:252,1675`; `DOM.enable` at `:493`). Domains auto-enable lazily only when you register
a handler for one of their events — so registering a `cdp.runtime.*` handler *does* reintroduce
the exact leak rebrowser patches exist to fix. (Inference from `_register_handlers`, mirrored in
zendriver `core/connection.py:588-638`.)

**Notable code worth stealing.** Honestly: little, and the input layer is a warning.
`Element.click()` (`core/element.py:393-414`) calls
`Runtime.callFunctionOn("(el) => el.click()", user_gesture=True)` — a **synthetic
`isTrusted: false`** click, strictly worse than Playwright's real `Input.dispatchMouseEvent`. The
trusted path, `Element.mouse_click()`, is documented as "this likely does not work atm"
(`core/element.py:515`). `send_keys` (`core/element.py:713-721`) sends only `char` events — **no
`keydown`/`keyup` fire at all**. `Tab.mouse_move` (`core/tab.py:1837-1860`) paths from the origin
rather than the current cursor and appends a stray `mouseReleased` after a plain move.

## zendriver

**Architecture.** Fork of nodriver at commit `1bb6003` (per `CHANGELOG.md`), same transport and
generated `cdp/` tree, but restructured: `Browser` is no longer itself a `Connection`, `config` is
deep-copied per instance so multiple browsers don't clobber each other, and process management
moved off `asyncio.subprocess` for Windows.

**Stealth-relevant choices.** Flag list grew to 17 (`core/config.py:119-137`), adding
`--disable-component-update`, `--disable-backgrounding-occluded-windows`,
`--disable-renderer-backgrounding`, `--disable-background-networking`. Config gained `user_agent`,
`disable_webrtc` (emitting the exact
`--webrtc-ip-handling-policy=disable_non_proxied_udp` + `--force-webrtc-ip-handling-policy` pair
ultrastealth already has at `fetcher.py:503-504`), and `disable_webgl`.

**Notable code worth stealing.** `core/keys.py` — a real key-event model with proper
`key`/`code`/`windowsVirtualKeyCode` triples, shift variants, modifier decomposition, and
grapheme/emoji clustering, consumed by `Element.send_keys` (`core/element.py:761-782`).
Ultrastealth gets equivalent coverage free from Playwright's keyboard layout map, so this is
reference material, not a port. `core/intercept.py` is a clean `Fetch`-domain context-manager
pattern worth mirroring if `browser_core.py` ever grows request interception.

## nodriver → zendriver delta

From `CHANGELOG.md` (the fork's own record — this is where the recent learning lives):

- **0.11.0** — complete keyboard rewrite (`KeyEvents`). Replaces nodriver's `char`-only
  keystrokes with real `keyDown`/`keyUp` sequences. *The single largest detection-relevant fix.*
- **0.15.0** — "Fix WebRTC IP Leaks"; adds `disable_webrtc` / `disable_webgl`.
- **0.10.0** — `user_agent` option specifically "to allow bypassing cloudflare javascript
  challenge in headless mode"; `core/cloudflare.py` walks shadow roots to locate the Turnstile
  iframe.
- **0.15.4** — `Connection._register_handlers` no longer re-enables already-manually-enabled
  domains (an over-enabling bug, i.e. extra CDP domain chatter = extra signal).
- **0.15.4** — `query_selector_all` now walks into nested iframes (CDP's `querySelectorAll` stops
  at document boundaries — a correctness gap in nodriver).
- **0.13.0 / 0.8.0** — Brave support; `--load-extension` re-enable flag for Chrome 136+.
- **0.12.0 / 0.5.0 / 0.4.0** — `Tab.intercept`, `expect_download`, `expect_request`/`expect_response`.
- Plus the boring-but-decisive stuff: `ruff` + `mypy` + `py.typed`, a real test suite including a
  browserscan.com bot-detection test, `uv` build. nodriver has none of that.

**Bottom line:** zendriver is nodriver with the input layer fixed, the WebRTC leak closed, and
engineering hygiene added. If you were going to adopt this school, you'd adopt zendriver — except
for the license (see below).

## pydoll

**Architecture.** Independent, not a nodriver derivative. `ConnectionHandler`
(`pydoll/connection/connection_handler.py`) owns the websocket; `commands/*.py` +
`protocol/*/methods.py` are hand-written typed command builders (not codegen). `Tab`
(`pydoll/browser/tab.py`, 2071 lines) is the op surface; `WebElement`
(`pydoll/elements/web_element.py`) is keyed by `Runtime` **objectId**, not `backendNodeId`.
Default flags: exactly two — `--no-first-run`, `--no-default-browser-check`
(`browser/managers/browser_options_manager.py:58-62`). Everything else is opt-in.

**Stealth-relevant choices.** `Tab.execute_script` (`browser/tab.py:1331`) exposes every
`Runtime.evaluate` parameter verbatim — including `unique_context_id`, `context_id`, and
`allow_unsafe_eval_blocked_by_csp` — so isolated-world execution is available but never forced.
`Runtime.enable` is opt-in via `Tab.enable_runtime_events()` (`browser/tab.py:406`), never called
at startup. `Page.addScriptToEvaluateOnNewDocument` fires only when the caller asks
(`browser/chromium/base.py:805`) — pydoll ships **no stealth bypass bundle at all**, in sharp
contrast to ultrastealth's `_native_mask.js` + per-bypass IIFE chain (`fetcher.py:396-421`). Its
README claim is purely "no WebDriver, no `navigator.webdriver`" — i.e. it relies on the absence of
artifacts rather than on masking them.

**Notable code worth stealing — this is the real find.** `pydoll/interactions/mouse.py` +
`interactions/utils.py` implement research-grounded pointer synthesis: **Fitts's Law** duration
(`fitts_duration`), **cubic Bézier** path with randomized perpendicular control points biased
toward the ballistic phase (`random_control_points`), **minimum-jerk** velocity profile
`10t³−15t⁴+6t⁵` (`minimum_jerk`), Gaussian **physiological tremor**, ~70%
**overshoot-and-correct** on long moves (`_move_with_overshoot`), ~12 ms frame pacing with
variance, randomized micro-pauses — all dispatched through real `Input.dispatchMouseEvent`, so
`event.isTrusted === true`. Every knob sits in one frozen `MouseTimingConfig` dataclass.
`interactions/keyboard.py` adds per-character delay distributions plus modelled **typos**
(adjacent-key, transpose, double, skip, missed-space) with realize-and-correct timing. **MIT**,
dependency-free (`math` + `random`), ~650 lines total.

## Artifacts ultrastealth hides that this school never creates

| Artifact | Why ultrastealth has it | Where it's handled today | Raw-CDP status |
|---|---|---|---|
| `globalThis.__pwInitScripts` | Created by the driver on every `addInitScript`, *before* user scripts run | Renamed on disk → `__execGuards` (`patch_rebrowser.py:92`) | Never created — no init-script dedup map exists |
| `UtilityScript` in `Error().stack` | Wraps every `page.evaluate` | Renamed → `ExecutionProxy` (`patch_rebrowser.py:96,99`) | Never created — `Runtime.evaluate` runs the raw expression (`nodriver/core/tab.py:889`) |
| `globalThis.__playwright_builtins__` | Every injected bundle (utility, clock, console API, WS mock, injected DOM script) calls `builtins(global)` | Renamed → `__nativeRefs` across 6 driver files (`patch_rebrowser.py:111-116`); still open upstream as rebrowser-patches#110 | Never created — no injected bundles at all |
| `Runtime.enable` execution-context leak | Playwright requires it; rebrowser patches work around it | `REBROWSER_PATCHES_RUNTIME_FIX_MODE` = `alwaysIsolated` (`fetcher.py:600`) or `addBinding` (`browser_core.py:19`) | Never sent by default; lazily enabled only if the user registers a Runtime event handler |
| `addBinding`-created `window` function | The `addBinding` workaround itself installs a page-visible binding | Accepted trade-off in `browser_core.py` | No binding needed |
| Node driver subprocess | Playwright's architecture | Always running | No Node process; pure Python + websocket |
| `--enable-automation` | Playwright default | Stripped via `ignore_default_args` (`fetcher.py:508`) | Never added — parity, no action needed |

**Counter-point ultrastealth wins:** Playwright talks over `--remote-debugging-pipe` (confirmed,
`chromium.js:253`), so **no TCP debug port is listening**. All three raw-CDP projects require
`--remote-debugging-port` and discover the socket over localhost HTTP; nodriver/zendriver also
add `--remote-allow-origins=*`. That is a real, observable difference in the opposite direction —
and none of the three supports pipe transport (grep-confirmed: zero hits for
`remote-debugging-pipe` across all three).

## Adoption options for ultrastealth

### A. Replace Playwright with a raw-CDP driver — **Reject.**

**What it'd take:** re-implement all 30+ MCP tools (`mcp_server.py:621-1317`) — cookies, storage,
state save/load, console/error capture, HTML extraction, init scripts — on raw CDP, *and*
re-derive auto-waiting from scratch (nodriver's `Tab.wait_for`, `core/tab.py:1326-1371`, is a naive
0.5 s poll with **no** actionability gate: no visible/stable/enabled/receives-events).
**What breaks:** every `page.get_by_role(...).nth(...)` in `browser_core._resolve`
(`browser_core.py:135-146`); `page.accessibility.snapshot()`; Firefox/WebKit; the whole
bypass-injection chain would need re-plumbing onto `Page.addScriptToEvaluateOnNewDocument`.
**Licensing kills it for nodriver/zendriver regardless:** both are **AGPL-3.0**
(`nodriver/LICENSE.txt`, `zendriver/LICENSE`, per-file headers at `nodriver/core/config.py:1-5`).
Vendoring either into MIT ultrastealth is not viable, and even depending on them arguably triggers
AGPL §13 for network-served use — which the MCP server is. **Effort: XL.**

### B. Add a raw-CDP runner behind `browser_core.py` — **Defer.**

**What it'd take:** `browser_core.py` is already the right seam — 19 ops in one `OPS` dict
(`browser_core.py:311-318`), one `_resolve()` abstraction, one `ensure_browser()` lifecycle. A
`runner="pydoll+temp-profile"` variant means a parallel `_resolve` plus a parallel implementation
of every op. Only pydoll is license-compatible. **What breaks:** `snapshot()` depends on
`page.accessibility.snapshot()` — you'd have to build the `eN` ref model on
`Accessibility.getFullAXTree` yourself (pydoll has the command at
`commands/accessibility_commands.py:97` but no tree-walker), and pydoll's `objectId` refs go stale
across navigation in a way `backendNodeId` does not. `wait()`'s five modes, `get(kind=...)` and
`is_(kind=...)` all need hand-rolled equivalents with no auto-waiting underneath. `daemon.py` and
`client.py` are transport-agnostic and would not change. **Payoff is small:** the artifacts tabled
above are *already neutralized* by `patch_rebrowser.py`, so a second backend buys marginal
residual stealth for a permanent 2× maintenance and test burden
(`tests/test_browser_core.py`, `tests/test_mcp_profiles.py`). **Effort: L. Recommendation: defer**
— revisit only if a benchmark site detects something `patch_rebrowser.py` demonstrably cannot cover.

### C. Cherry-pick techniques — **Yes. Do this.**

Pydoll's MIT licence makes its input layer directly vendorable. Nothing else in the three repos
beats what ultrastealth already has. **Effort: S–M. Recommendation: adopt.**

## Recommended actions

1. **Port pydoll's humanized pointer synthesis into `browser_core.py`.** Vendor
   `pydoll/interactions/utils.py` (`bezier_2d`, `minimum_jerk`, `fitts_duration`,
   `random_control_points` — ~170 lines, stdlib-only, MIT) plus `MouseTimingConfig` and the
   `_move_humanized` / `_move_with_overshoot` / `_perform_movement_loop` logic from
   `pydoll/interactions/mouse.py`. Drive it through `page.mouse.move/down/up` so events stay
   `isTrusted`, behind a `humanize: bool = False` kwarg on `click()`/`hover()`
   (`browser_core.py:179`, `:210`). *Rationale:* ultrastealth's click is a bare Playwright click —
   trusted, but teleporting cursor, zero dwell, zero tremor, which behavioural-biometric vendors
   profile directly. Largest real gap this school exposes, and fully orthogonal to the driver.
   MIT→MIT; keep attribution in a header comment. **Effort: M.**
2. **Port pydoll's typing cadence into `type_text`.** `browser_core.py:189` uses a flat
   `delay=20`. Replace with the per-character distribution from
   `pydoll/interactions/keyboard.py:378` (`_apply_realistic_delay`), behind the same `humanize`
   flag. Skip the typo simulation — clever, but it risks corrupting form data in an agent loop.
   **Effort: S.**
3. **Audit the flags they add that ultrastealth omits.** Confirm `--remote-allow-origins=*` and
   `--disable-features=IsolateOrigins,site-per-process` stay omitted (both weaken the browser and
   the latter is plausibly detectable), and that `--disable-webgl`/`--disable-webgl2`
   (zendriver 0.15.0) remain unwanted since ultrastealth *spoofs* WebGL rather than disabling it.
   Note the decision at `fetcher.py:424`. **Effort: S.**
4. **Record the pipe-vs-port advantage.** Add a line to `README.md`'s stealth section: Playwright's
   `--remote-debugging-pipe` default means ultrastealth exposes no listening CDP port, unlike every
   raw-CDP driver — and therefore `ULTRASTEALTH_CDP_PORT` (`fetcher.py:615-619`) is a
   *stealth-reducing* opt-in and should say so. **Effort: S.**
5. **Mirror zendriver's `core/intercept.py` context-manager shape** if/when `browser_core.py` gains
   request interception. Design reference only — do not copy code (AGPL). **Effort: S, deferred.**
6. **Do not vendor, fork, or depend on nodriver or zendriver.** Both AGPL-3.0; read for ideas,
   re-implement independently or use pydoll. **Standing constraint.**
