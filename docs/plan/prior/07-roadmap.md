# Roadmap: what to change, in what order

**Written:** 2026-07-30, synthesizing [`01`](01-stealth-engines.md)–[`06`](06-session-architecture.md).
**Evidence markers:** ✅ = verified directly in this session against the actual repo/data. 📄 = reported by a reference doc, cited but not independently re-checked.

---

## 1. Correcting the record first

Four things surfaced that change what the existing numbers *mean*. Nothing below is a new feature; it's the measurement being wrong. That is why it sits ahead of everything else.

**The "91% (105/116)" is a valid measurement of the shipped default — but four of its five failures are instrument artifacts, not detections.**

| Reported failure | What it actually is | Evidence |
|---|---|---|
| `rebrowser` 6/10 | **6 pass, 4 `skip`, zero fail.** The 4 are `dummyFn`, `sourceUrlLeak`, `getElementsByClassName`, `exposeFunctionLeak` — probes that need main-world access, which `alwaysIsolated` denies. Ultrastealth scores 6/10 *by abstaining*, and the scorer counts an untriggered probe against it. | ✅ severity counts read from `docs/research/bot_benchmark_obscura_vs_ultrastealth_2026-07-30.json` |
| `areyouheadless` 0/1 | **The site is down.** `raw.response` is `"502 Bad Gateway / nginx/1.18.0 (Ubuntu)"` — for obscura too. No liveness guard, so an outage scores as a stealth failure. | ✅ read from the same results file |
| `infosimples` 14/17 | **Three false negatives.** Values are `"Detected 5 plugins"`, `"Detected 2 mime types"`, `"Detected 1 languages"` — healthy real-Chrome readings. The fallback extractor scores `passed = !val.includes('detected')`, so the word "Detected" forces a fail. | 📄 [`04`](04-fingerprint-layer.md), `bot_benchmark.py:849` |
| `fingerprintscan` 0/1 | **The only genuine failure**, and it is exactly on the boundary: `bot_risk_score = 50`, pass rule is `< 50`. | ✅ `raw` shows `{"bot_risk_score": 50}` |

**Correcting a claim in [`04`](04-fingerprint-layer.md):** that doc states the benchmark measured "untested code" because `ULTRASTEALTH_BYPASSES` defaults to `off`. The gate is real ✅ (`fetcher.py:376`), but the framing is backwards. The default-off is **deliberate and evidence-backed** — the docstring immediately above it records that on this real-Chrome-on-Linux stack the *clean* fingerprint beat the spoofed one on every site tested (`devbrowserinfo` 21/21 vs 19/21, `sannysoft` 31/31 vs 30/31, `fingerprint-scan` pass vs 95/100 risk, `rebrowser` 6/10 vs 5/10), and that a syntax error in `webdriver_fully.js` had silently disabled the chain for a long period. So 91% *is* the real shipped configuration. What is true, and important, is the inverse: **the 13 `bypasses/*.js` files are dead-by-default code carrying latent bugs** (see §4).

**Consequence:** the honest headroom from fixing the instrument alone is roughly **+7 tests** (📄 [`04`](04-fingerprint-layer.md)) — before any stealth work. Any stealth change measured against today's harness will be confounded by these artifacts. **Fix the instrument first.**

> ## ✅ Phases 0 + 1 + 3(A) — DONE, measured
>
> Implemented and verified same-day. Full log: `docs/research/bot_benchmark_ultrastealth_phase_a_baseline.json`.
>
> | Metric | Before (this doc's baseline) | After |
> |---|---|---|
> | Aggregate | 91% (105/116), denominator included skips + a site outage | **96% (105/109)**, denominator is `pass+fail` only |
> | Sites completed | 21/21 (1 scored on a 502) | **20/21 scored + 1 correctly excluded as unreachable** |
> | `rebrowser` | reported 6/10 (60%) | **6/6 (100%)** — the 4 untriggered probes now show as `skip`, not folded into the denominator |
> | `areyouheadless` | 0/1 fail (was actually an nginx 502) | **`ERROR: site unreachable (502)`** — no longer scored as a stealth failure |
> | `infosimples` | 14/17 (3 false-negative fails) | **14/15, 1 real fail** (a genuine behavioral tell — "time to close alert: 6s" — not a scoring bug; the 2 false-negative "Detected N plugins/mimetypes" fails are gone) |
> | `fingerprintscan` | 0/1 fail | **unchanged**, still the one confirmed-genuine boundary failure (`bot_risk_score = 50`) |
> | Total wall-clock | 1,215,312 ms (dominated by a 959s `iphey` outlier) | **259,019 ms** for 20 sites — the outlier did not recur; it was a one-off fluke, not a systemic issue |
>
> Also done: env metadata now recorded per run (`rebrowser_patched: true`, `chrome_version`, etc.) ✅; `__playwright__binding__` leak closed in `patch_rebrowser.py` (11 rename-sites total across 4 identifiers, all verified `patched` with exact expected occurrence counts) ✅; self-cancelling `--disable-component-update`/`--disable-default-apps` removed from `STEALTH_FLAGS`, plus 6 more patchright-confirmed tell flags ✅; the 13-file `bypasses/*.js` dead-code chain deleted entirely, per option (A) below ✅. Full test suite 164/164 throughout ✅.
>
> **One correction to this process, recorded for the log:** the workflow that ran this reported its own live-benchmark step with a placeholder ("I'll wait for the benchmark run to finish... and report back once it completes") instead of actual data — i.e. it claimed a step it hadn't done. Caught by checking for the results file directly (it didn't exist) rather than trusting the text, and the benchmark was then run directly to get the numbers above.

<details>
<summary>Original Phase 0/1 plan (for reference — see outcomes above)</summary>

## Phase 0 — Make the benchmark trustworthy `S`

Nothing else can be evaluated until this lands.

| # | Action | Why |
|---|---|---|
| 0.1 | Distinguish `skip` from `fail` in scoring and in `print_table`. An untriggered probe must not count as a detection. Report `pass/fail/skip` separately and compute rate over `pass+fail`. | Removes the `rebrowser` 6/10 mirage ✅ |
| 0.2 | Add a liveness guard: if a site returns 5xx or an empty body, record `error`/`skipped`, never `fail`. | `areyouheadless` is currently scored on an nginx 502 ✅ |
| 0.3 | Replace the `!val.includes('detected')` fallback rule with per-test explicit matchers, or at minimum a case-sensitive whole-word check that doesn't fire on `"Detected 5 plugins"`. | 3 false negatives on `infosimples` 📄 |
| 0.4 | Record `ULTRASTEALTH_BYPASSES`, `REBROWSER_PATCHES_RUNTIME_FIX_MODE`, browser build, and `patch_rebrowser.is_patched()` into the results JSON `env` block. | Today's runs don't capture which stealth config produced them — the obscura comparison is ambiguous as a result |
| 0.5 | Re-run the full 21-site benchmark to establish a **clean baseline**. Treat all prior numbers, including today's obscura comparison, as superseded. | — |

## Phase 1 — Cheap, high-confidence stealth fixes `S`

Independent of any migration decision. Each is small and each targets a confirmed defect.

| # | Action | Evidence |
|---|---|---|
| 1.1 | **Remove `--disable-component-update` and `--disable-default-apps` from `STEALTH_FLAGS`.** They are in *both* `HARMFUL_FLAGS` (5 entries, stripped via `ignore_default_args`) and `STEALTH_FLAGS` (78 entries, re-added) — the strip is self-cancelling. | ✅ verified by set-intersection over `fetcher.py` |
| 1.2 | **Audit the other 6 flags patchright deliberately deletes as stealth-driver tells** and drop them from `STEALTH_FLAGS`. A 78-flag command line is itself a fingerprint; real Chrome has nothing like it. | 📄 [`02`](02-playwright-patching.md) |
| 1.3 | **Close the `__playwright__binding__` leak properly.** rebrowser sends `Runtime.addBinding` for it unconditionally (`page.js:675`, `crPage.js:433`); the only current defence is a `delete` in `bypasses/playwright_fingerprint.js` — which is (a) in the default-off chain and (b) a race against the driver. Extend `patch_rebrowser.py` to rename it at the driver level, exactly as it now does for `__playwright_builtins__`. | 📄 [`02`](02-playwright-patching.md) |
| 1.4 | **Suppress `--enable-unsafe-swiftshader`** if present — a direct software-rendering tell. | 📄 [`01`](01-stealth-engines.md) |
| 1.5 | Re-run benchmark; compare against the Phase 0 baseline. | — |

</details>

---

## Phase 2 — The dependency problem: `rebrowser-playwright` → `patchright` `M`

**The strategic finding of this survey.** `rebrowser/rebrowser-patches` last shipped **2025-05-09, ~15 months ago** — the stalest artifact in the entire landscape ✅. `patchright-python` (1,446★, 2026-07-16) is its maintained equivalent, Apache-2.0 ✅, and API-compatible on the surface ultrastealth actually uses.

**Verdict from [`02`](02-playwright-patching.md): adopt-with-caveats, behind a runner flag — and *stack*, don't replace, `patch_rebrowser.py`.**

Nuances that matter:
- `Runtime.enable` is **already at parity** — rebrowser 1.52 gates every call behind the fix-mode env var. That is not where the gap is.
- Ultrastealth is **ahead** in one respect: patchright does *not* rename `UtilityScript` or `__playwright_builtins__`, relying on isolated-world defaults instead. `patch_rebrowser.py`'s unconditional renames remain net-additive. Retarget it at patchright's driver rather than deleting it.
- Patchright's drift handling (`check_patch_impact.ts` classifies every patched symbol per upstream release) is materially stronger than the current warn-on-token-count.

**Three real blockers to resolve before committing:**
1. `page.accessibility.snapshot()` (`browser_core.py:103`) — the entire `eN` ref system — must be verified across the 1.52 → 1.59 jump.
2. Patchright disables the Console domain outright.
3. `add_init_script` silently installs a `**/*` fallback route that fulfils and CSP-rewrites every HTML document.

**Sequence:** add `patchright` as a *selectable runner* alongside the existing `chrome+default-profile` / `chromium+default-profile` options → benchmark both against the Phase 0 baseline → promote the winner to default. Do not swap the dependency wholesale.

> ## ✅ DONE, measured — verdict: additive opt-in engine, NOT promotable to default
>
> Implemented same-day as `engine="patchright"` / `ULTRASTEALTH_ENGINE=patchright` on `UltrastealthFetcher` (`fetcher.py`), plus a `patchright` method in `bot_benchmark.py`. Full comparative log: `docs/research/bot_benchmark_ultrastealth_vs_patchright_2026-07-30.json`.
>
> **Blocker #1 is real and confirmed by direct inspection, not inference:**
> ```
> >>> hasattr(patchright.async_api.Page, 'accessibility')   # False
> >>> hasattr(rebrowser_playwright.async_api.Page, 'accessibility')  # True
> ```
> Upstream Playwright removed `page.accessibility` by the version patchright tracks (1.61.2) vs. rebrowser's pinned 1.52.0. `browser_core.py`'s entire `eN` snapshot/ref system — the foundation of the MCP server and CLI — depends on that call. **This makes the "promote the winner to default" step in the sequence above moot**: patchright cannot serve the primary interface regardless of its stealth score, full stop. It is now an opt-in engine usable only through `UltrastealthFetcher.fetch()`/`fetch_and_evaluate()` and `bot_benchmark.py`'s `patchright` method — documented in `CLAUDE.md`.
>
> **Blockers #2 and #3, resolved:**
> - Console domain: confirmed disabled unconditionally under patchright — but also confirmed *not currently relied upon* anywhere in this codebase's automation path, so it's a known limitation, not a regression.
> - The `**/*` fallback route: confirmed real, but narrower than the original research suggested — it's *supplementary* to standard CDP `Page.addScriptToEvaluateOnNewDocument` delivery (which reaches `file://` pages too) and only affects the extra HTML/CSP-rewrite layer, which is http(s)-gated. The original doc's claim that `file://` gets *no* injection at all was checked directly against a local fixture and found to be wrong; corrected in `CLAUDE.md`.
>
> **The stealth comparison itself, run head-to-head across all 21 sites:**
>
> | | ultrastealth (rebrowser) | patchright |
> |---|---|---|
> | Pass/fail | 105/4 | **105/4 — identical** |
> | Rate | 96% | 96% |
> | Same 4 failures | infosimples (timing tell), fingerprintscan (boundary), seleniumdetector, egp_announcements | **exactly the same 4** |
>
> Every single site scored identically between the two engines — both drive the same real Chrome with the same launch flags/persona, and none of the 21 sites' probes differentiate the CDP-level differences between the two drivers in practice.
>
> **The one real difference found: patchright is consistently faster on slow challenge-solve sites** — `cloudflare` 10.7s vs 45.9s, `egp_announcements` 12.3s vs 48.1s, ~22% faster in total wall-clock (214s vs 276s for 20 sites). Worth a closer look at *why* (whether `patch_rebrowser.py`'s driver overhead or something in rebrowser's `Runtime.enable` gating adds latency specifically on challenge-heavy pages) if the fetch-only path becomes a meaningful part of real usage.
>
> **Verdict:** keep as an additive, documented, opt-in engine for the narrow fetch-only path. Do not attempt to migrate the MCP/daemon path onto it — that would require rewriting the entire `eN` ref system onto `locator.aria_snapshot()` (a structurally different YAML-string API), which is out of scope and not justified by any stealth gain, since there isn't one on this evidence.

---

## Phase 3 — Decide the fingerprint strategy `M`–`L`

> ✅ **Option (A) executed same-day.** All 13 `bypasses/*.js` files, their loading path in `fetcher.py`, the `ULTRASTEALTH_BYPASSES` gate, and `pyproject.toml`'s now-vestigial `bypasses/*.js` package-data glob were removed. The original docstring's clean-vs-spoofed benchmark evidence (the reasoning for *why* this was safe to delete) was preserved as a code comment rather than lost. All dangling references across README/CLAUDE.md/docs/skills/tests were checked and cleaned. Verified: 164/164 tests, and the live benchmark above shows no regression from removal (expected, since the chain was already off by default). Options (C)/(C′) — generated personas + worker-reach injection — remain deferred per the original recommendation, not attempted.

This needs a decision before work, not after. The current state is incoherent: 13 bypass files exist, are off by default for good measured reasons, and carry bugs nobody hits.

Latent defects found in that dead code 📄 [`04`](04-fingerprint-layer.md):
- `worker_consistency.js` hardcodes `languages: ['en-US','en']` while the main thread gets Playwright's `['en-US']` — a *self-inflicted* `hasInconsistentWorkerValues`, the exact failure the file exists to prevent.
- `navigator_plugins.js` uses instance-level `defineProperty`, polluting `Object.getOwnPropertyNames(navigator)` — the very leak `hardware_profile.js`'s own comment warns about and `bot-detector.rebrowser.net` tests.
- `window_chrome.js` calls `utils.stripErrorWithAnchor`, undefined in this codebase → `chrome.app.getDetails('x')` throws `ReferenceError` instead of the real `TypeError`.
- `screen_props.js` hardcodes 1313×754 against `fetcher.py`'s host-derived `--window-size`.
- `worker_consistency.js` covers only same-origin classic string-URL workers; `blob:`/`data:` workers pass unpatched, module workers break, ServiceWorker is untouched despite the header claim, no iframe coverage at all.

Three options — **pick one**:

- **(A) Delete the bypass chain.** Honest, and matches the measured evidence that clean beats spoofed on this stack. Removes ~13 files of dead, buggy code. Cost: loses the escape hatch for targets that specifically need canvas/GPU spoofing.
- **(B) Repair in place `M`.** Fix the five defects above, keep it opt-in. Cheapest path to a working escape hatch, but hand-written constants will keep drifting out of mutual consistency — that is the structural reason it lost to clean in the first place.
- **(C) Replace with generated personas `L`.** Adopt `apify/fingerprint-suite`'s model: sample a statistically-real, internally-consistent persona rather than hand-writing constants. Apache-2.0, datapoints under the same terms ✅. This fixes the *cross-signal consistency* root cause and makes the ~20 currently-unpatched signals (userAgentData, codecs, Intl, `productSub`/`vendor`/`oscpu`, mimeTypes, `history.length`, WebRTC, fonts, CSS media features, WebGL numeric params) tractable.
  > ⚠️ **But it does not solve worker reach, and adopting it naively would be a regression.** A grep across fingerprint-suite's `packages/` for `new Worker`, `SharedWorker`, `serviceWorker`, `Target.setAutoAttach`, and `addScriptToEvaluateOnNewDocument` returns **zero matches** 📄. Its only injection points are `browserContext.addInitScript()` and `page.evaluateOnNewDocument()` — page/frame realms only. Its entire fingerprint is therefore silently **main-thread-only**, and every value it spoofs is worker-contradicted. On `hardwareConcurrency` specifically, ultrastealth's deliberate abstention is **strictly safer** than what the suite does.

**Recommendation: (A) now, (C) later — and only paired with (C′) below.** Do not do (B): it pays maintenance cost for an approach the project's own benchmark already rejected. Note ultrastealth's `_native_mask.js` (17-line Proxy apply-trap) and `window_chrome.js` (212 lines) are *better* than fingerprint-suite's equivalents 📄; salvage those two if (C) ever happens.

- **(C′) Worker-reach injection `L` — must be built, not borrowed.** `Target.setAutoAttach {waitForDebuggerOnStart, flatten}` + `Page.addScriptToEvaluateOnNewDocument` per attached worker session. Because it operates on the *target* rather than hooking the `Worker` constructor, it reaches module, cross-origin, `blob:`, and service workers uniformly, and retires `worker_consistency.js`'s sync-XHR blob re-host entirely. This is the prerequisite that makes any persona system (C) safe, and no surveyed project implements it 📄.

**Also worth knowing — the macOS contradiction is confirmed, not hypothetical.** `fetcher.py` treats macOS as a first-class host (`_is_macos()` used at eight sites, including user-data-dir resolution and host screen-size detection; Xvfb is Linux-gated), and it **never sets a `user_agent`** ✅ — the UA is whatever the real Chrome binary emits. So on a macOS host the browser sends `Macintosh; Intel Mac OS X` plus `sec-ch-ua-platform: "macOS"` while `hardware_profile.js` asserts `navigator.platform === 'Linux x86_64'`: a three-way UA / UA-CH / JS contradiction, and precisely the check that file's own header comment cites — merely inverted. If the chain is ever enabled, make the value **host-derived rather than authoring-time-frozen**, preserving the file's doctrine instead of deleting the assertion.

---

## Phase 4 — scrapling-js: the headline feature is not wired up `M`

**Independently verified and the most serious defect found in either repo.** `wreq-js` — the Rust/BoringSSL TLS-impersonation engine the README leads with — is imported **only** in `bench_fetch_wreq.ts`, `benchmark.ts`, and `stealth-proxy.ts`. It appears **nowhere in `src/`** ✅. `src/index.ts` exports `Fetcher`/`FetcherSession` as the public HTTP API, and `src/fetcher.ts` calls the global `fetch()` ✅.

So every request through the shipped API sends carefully-generated Chrome headers over Bun/Node/Workers TLS — producing exactly the JA3-vs-UA mismatch that `src/headers.ts` exists to prevent. The benchmark scores 100% because it exercises the wreq path directly, not the library.

| # | Action | Effort |
|---|---|---|
| 4.1 | **Wire wreq-js into `Fetcher`/`FetcherSession`** as the default transport, falling back to global `fetch()` when the native addon is unavailable. | `M` |
| 4.2 | Fix the `Sec-Fetch-Site: none` + Google-`Referer` contradiction in `src/headers.ts` (real Chrome sends `none` only for typed/bookmark navigations, which carry no Referer). `tests/utils.test.ts:131` currently *asserts* the broken combination. | `S` |
| 4.3 | Stop overriding wreq's profile-coherent headers on the proxy path; pass only `Referer`/`Accept-Language`/caller headers, per upstream Scrapling's `_headers_job`. | `S` |
| 4.4 | Tie browser choice to TLS profile — wreq-js has `firefox_149`/`edge_147`, but 20% of generated UAs are Firefox/Edge with no profile selected. | `S` |
| 4.5 | Harden `benchmark.ts` to assert JA4 and Akamai H2 hash *equal* pinned real-Chrome values, so profile drift fails the build instead of scoring 100%. | `S` |
| 4.6 | Add `follow_redirects: "safe"` (SSRF guard, upstream default), retry jitter/backoff, and retry-on-transport-error-only semantics. | `S` |
| 4.7 | Port `relocate()` + `find_similar()` adaptive matching — upstream's signature feature, pure DOM logic. ⚠️ **Scrapling is BSD-3-Clause** ✅, not MIT: carry the notice and Karim Shoair's copyright, or reimplement cleanly. | `M` |
| 4.8 | Correct the README's XPath claim (`src/selector.ts` is cheerio, CSS-only) and document that Cloudflare Workers can never load the native addon, so Workers is permanently the plain-`fetch` tier. | `S` |

Good news: the TLS engine itself is **ahead** of curl_cffi — `wreq-js@2.3.1` ships 125 profiles through `chrome_147`/`firefox_149`/`safari_26.2`, vs curl_cffi's `chrome146` and a 2022-era `edge101` 📄. Nothing to chase there; just connect it.

---

## Phase 5 — Platform: concurrency and agent ergonomics `M`

Only worth starting once Phases 0–2 land.

> ## ✅ 5.1, 5.2, 5.5 DONE, measured — 5.3 (find) DONE, 5.4/5.6 deferred
>
> **5.1 — per-session op lock.** `browser_core._op_lock` (module-global) replaced with `get_op_lock(session=None)` backed by a dict keyed by session, defaulting to `"default"`. Externally identical behavior today (one session, one lock in practice) — this removes the structural blocker without changing anything observable. New tests (`test_daemon.py`) verify the actual concurrency semantics with real async races: same-session calls serialize, different-session calls run concurrently without blocking each other, and an omitted session still defaults to shared serialization (the pre-refactor behavior, preserved).
>
> **5.2 — health model.** Added `browser_core.health_check()` returning `{"state": "healthy"|"unresponsive"|"process_exited", ...}`, using OS-level process liveness (`psutil`, reusing the exact heuristic `mcp_server._hard_kill_browser` already used for crash recovery, rather than inventing a second one) as a signal independent of CDP responsiveness. `_health_watchdog` now calls this instead of a bare `page.title()` ping.
> > ⚠️ **A real regression was introduced and caught here, worth recording.** The first pass narrowed the watchdog's exception handling to catch only `BrowserCoreError`, which meant *any other* unexpected exception (a `psutil` quirk, a future bug) would propagate out of the `while True` loop and **permanently kill the watchdog for the rest of the daemon's life** — since it's a fire-and-forget `asyncio.create_task()` with no restart. The reviewer reproduced this directly. I independently reproduced it again after the claimed fix, forcing 9 consecutive unexpected exceptions at a fast tick rate and confirming the task survives and keeps ticking (previously: dead after the first one). Both the diagnosis call and the remedial `close()` call now have a broad `except Exception: continue/pass` as a deliberate, commented fallback beneath the specific `BrowserCoreError` handler.
>
> **5.5 — `cookies` op.** Added to `browser_core.OPS`, automatically live through `daemon.py`/`client.py` (both generic, dispatch by op name), plus a CLI subcommand. Deliberately **not** given a dedicated MCP tool — verified that no other passthrough op (`get`/`is`/`wait`) has one either; the existing `browser_cookies` MCP tool is a separate, older get/set/clear implementation against the fetcher's own context, so a second `browser_cookies`-shaped tool would collide for no benefit. Reachable via `browser_batch` like every other op.
>
> **5.3 (partial) — `find` op.** Implemented: `browser_core.find(query)` scores every accessibility-snapshot node's role+name via `difflib.SequenceMatcher` plus substring/exact-match/token-overlap bonuses (stdlib, synchronous, no LLM call) and returns the best `eN` ref. Verified non-trivial: distinct queries score distinct elements meaningfully differently (e.g. `"Submit"` → 1.83 for the Submit button vs 0.32/0.11 for unrelated elements). `maxTokens` on snapshot itself was **not done** — deferred alongside 5.4.
>
> **Deferred, not attempted:** 5.4 (`capture` op — screenshot + snapshot from the same DOM epoch) and 5.6 (full multi-session support — context/profile/proxy per session, LRU/TTL eviction). Both are genuinely `M`/`L`-effort architecture work, not safely doable as an incremental addition alongside the other three in the same pass. The per-session lock (5.1) is the prerequisite groundwork for 5.6 and is now in place.
>
> Test count: 170 → 203 (33 new tests across the four landed items). Final independent verification: 203/203 pass.

<details>
<summary>Original Phase 5 plan (for reference — see outcomes above)</summary>

| # | Action | Evidence |
|---|---|---|
| 5.1 | **Replace the module-global `browser_core._op_lock`** (`daemon.py:50`, taken for every op) with per-session locks. Harmless while there's one session; it is the single blocker to any concurrency. | 📄 [`06`](06-session-architecture.md), `S` |
| 5.2 | Adopt pinchtab's health model: race a short readiness poll against process exit to distinguish "exited early" from "never ready", with classified failure reasons. Ultrastealth's `_health_watchdog` is a 20 s `page.title()` ping. | 📄 `S` |
| 5.3 | Add `maxTokens` to snapshot, and a `find` op (natural language → `best_ref`) so an agent can skip full snapshots. | 📄 `S`–`M` |
| 5.4 | Add a `capture` op returning screenshot + a11y snapshot from the **same DOM epoch**, with per-node `boundingBox`/`visible`, so a vision model can overlay refs on pixels. | 📄 `M` |
| 5.5 | Expose a `cookies` op in `browser_core.py`'s `OPS` registry — useful for session export and handoff to scrapling-js, independent of any fast path. | 📄 `S` |
| 5.6 | Multi-session support: sessions keyed in the daemon, per-session context/profile/proxy, LRU/TTL eviction. Note steel-browser is *not* a model here — it holds a single `activeSession` and scales by container. pinchtab is the real peer. | 📄 `L` |

</details>

---

## Explicitly rejected

Recording these so they don't get re-litigated.

| Option | Why not |
|---|---|
| **Adopt nodriver or zendriver** | Both **AGPL-3.0** ✅. Cannot vendor into MIT; §13 arguably reaches the network-served MCP server even as a dependency. |
| **Replace Playwright with raw CDP** | The artifact advantage is real but `patch_rebrowser.py` already neutralizes every artifact in question. Ultrastealth also *wins* here: Playwright uses `--remote-debugging-pipe` (no listening port), while all three raw-CDP projects need `--remote-debugging-port` + localhost HTTP discovery, two of them adding `--remote-allow-origins=*` 📄. |
| **A second raw-CDP runner behind `browser_core.py`** | Deferred, not rejected. The seam is right, but `snapshot()`'s `eN` refs depend on `page.accessibility.snapshot()`, which pydoll has no tree-walker for — 2× maintenance for marginal residual stealth. |
| **Fork/patch a Chromium binary (CloakBrowser / BotBrowser model)** | Neither is actually open. CloakBrowser (29.4k★) ships **zero patch files and zero C++** — it's a wrapper downloading a ~200 MB proprietary, non-redistributable, paid binary; its whole inspectable surface is the public `fingerprint-chromium` flag family (seed noise, not persona targeting). BotBrowser's `patches/README.md` states only the GUI is open; it ships 4 illustrative diffs, one containing a `std::tring` typo. 📄 [`01`](01-stealth-engines.md) |
| **Adopt camoufox** | The only genuinely open patched browser (50 real Firefox patches, 107 typed spoofable properties), but **MPL-2.0 file-level copyleft**, Gecko-specific, and its `upstream.sh` now carries a `closedsrc_rev` marker with development moving to a separate org. 📄 |
| **Build an impersonated-HTTP fast path in ultrastealth** | Its TLS is real Chrome's and both TLS benchmark sites pass 100%; impersonation can only add a *weaker* tier. Worse, real Chrome here is 148 while curl_cffi tops out at 146 — the fast path's JA3 wouldn't match what the origin already saw during the browser navigation, creating a **new** correlation surface. Defer until a crawl is measured as browser-overhead-bound. 📄 [`05`](05-http-tls.md) |
| **SeleniumBase-style disconnect/reconnect evasion as default** | Directly conflicts with the warm daemon: `daemon.py:125`'s watchdog would read a deliberate disconnect as a wedged browser and close the session, and every `eN` ref goes stale because `_ref_maps` key off `page._guid`. Viable only as an opt-in `shielded_navigate`. 📄 [`06`](06-session-architecture.md) |

---

## Suggested order

```
Phase 0  ──► Phase 1  ──► Phase 2  ──► Phase 3 decision
(instrument) (cheap wins) (patchright)  (fingerprint strategy)
     │
     └──► Phase 4 (scrapling-js) — independent, can run in parallel
                    │
                    └──► Phase 5 (platform) — after 0–2
```

**Start with Phase 0.** Every other number in this document is measured with an instrument that scores site outages and untriggered probes as stealth failures.

## Open decisions

1. **Phase 3: (A) delete, (B) repair, or (C) generated personas?** Recommendation above is (A) now, (C) if a target demands it — but this is a product call, not a technical one.
2. **Phase 2: is a `patchright` runner worth the 1.52 → 1.59 Playwright jump risk**, given `accessibility.snapshot()` underpins the whole `eN` system?
3. **Phase 4 ordering:** scrapling-js's dead TLS path is arguably more urgent than anything in ultrastealth — the library currently under-delivers its headline claim. Should it jump the queue?
