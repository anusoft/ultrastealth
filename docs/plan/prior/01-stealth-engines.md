# Prior art: patched-browser stealth engines

> Ultrastealth spoofs from inside the JS heap of a stock Chrome: every value it changes is produced
> by a JavaScript function that other JavaScript can, in principle, catch. This school moves the same
> spoofs *below* the JS boundary — into Blink getters, the layout engine, HarfBuzz, the ICE agent, and
> V8 — so the spoofed value is just what the browser natively returns. That buys three things
> injection structurally cannot reach: **(1)** surfaces with no JS API to hook (CSS media queries, ICE
> candidate generation, HTTP/2 and TLS, font shaping); **(2)** *behavioral* tells where faking the
> return value doesn't fake the cost of computing it (execution timing, V8 recursion depth, real GPU
> pixel output); **(3)** universal coverage — a C++ getter is already correct in module workers,
> cross-origin iframes, and OOPIFs, where an init script may never run.

## Comparison at a glance

| Project | Stars | Last commit (clone) | Stealth layer | Ships binary? | License | Closest ultrastealth analogue |
|---|---|---|---|---|---|---|
| CloakHQ/CloakBrowser | 29.4k | 2026-07-30 (`v0.5.3`) | Closed C++ Chromium fork, driven by CLI flags | Yes, ~200 MB, 5 platform tags | Wrapper MIT; **binary proprietary + paid** | `fetcher.py` launch wrapper |
| daijro/camoufox | 10.6k | 2026-07-18 | 50 real Firefox `.patch` files + C++ config layer | Yes, self-buildable | **MPL-2.0** (file-level copyleft) | `bypasses/*.js` + `hardware_profile.js`, but native |
| botswin/BotBrowser | 2.6k | 2026-07-30 | Closed Chromium fork, encrypted profile blobs | Yes, tiered/subscription | MIT file, but **engine not in repo** | `bypasses/` concept, tiered and closed |

---

## CloakBrowser — 29.4k

**Approach.** Wrapper repo plus marketing. The README claims "71 source-level C++ patches", but
**there is not one patch file, `.diff`, or line of C++ in the clone** — `git ls-files` returns only
Python, TypeScript, and C# wrapper code. The entire inspectable stealth surface is a handful of CLI
flags into a closed binary downloaded on first run (~200 MB). `BINARY-LICENSE.md:1-30` confirms the
split: MIT wrapper, proprietary binary, *latest major requires an active paid subscription*.

**Key techniques (what is actually inspectable)**
- `cloakbrowser/config.py:60-84` — `get_default_stealth_args()` emits `--fingerprint=<random seed>` +
  `--fingerprint-platform={macos|windows}`. Full vocabulary (from `browser.py`):
  `--fingerprint{,-platform,-locale,-timezone,-webrtc-ip,-windows-font-metrics}`,
  `--ignore-gpu-blocklist`. This matches the public `fingerprint-chromium` patch family, so the model
  is **seed-based, not persona-based** — per-seed noise, not "be this exact MacBook".
- `config.py:52` — `IGNORE_DEFAULT_ARGS = ["--enable-automation", "--enable-unsafe-swiftshader"]`.
  The SwiftShader suppression is a free catch: Playwright's default forces software WebGL, producing
  a renderer string no real user has.
- `config.py:22-28` — per-platform version pinning shows the binary-shipping tax in their own source:
  linux/windows on `146.x`, **both macOS tags stuck on `145.x`**.
- MIT and directly borrowable: `cloakbrowser/human/mouse.py` (cubic-Bézier cursor paths,
  `_bezier`/`_random_control_points`); `cloakbrowser/geoip.py` (`resolve_proxy_geo_with_ip()` —
  MaxMind lookup of the proxy exit IP for timezone + locale); `js/src/fonts.ts`
  (`WINDOWS_FONT_TELLS`/`OFFICE_FONT_TELLS`, an `fc-list` check warning when a Windows-spoofing Linux
  host lacks Windows fonts); `config.py:39-42` (Ed25519 verification of downloads).

**What ultrastealth can't do from JS injection.** Nothing *verifiable here* — the interesting layer is
compiled away. Inferring from flag names only: `--fingerprint-webrtc-ip` implies ICE rewriting in the
network service, `--fingerprint-windows-font-metrics` implies shaper-level text measurement. Neither
is coherently reachable from JS.

**Adoption cost / licensing.** The binary is a hard no: `BINARY-LICENSE.md` forbids redistribution,
repackaging, modification, and reverse engineering, and requires an OEM/SaaS license to expose it to
third parties. The MIT wrapper code *is* freely portable.

---

## camoufox — 10.6k

**Approach.** The only genuinely open member of this school. 50 real `.patch` files against Firefox,
applied via a `Makefile` build, with a bespoke C++ config plumbing layer. Configuration arrives as
JSON in the `CAMOU_CONFIG` env var, parsed once in `additions/camoucfg/MaskConfig.hpp:48-86`
(chunked across `CAMOU_CONFIG_1..N` to dodge env-size limits) and read from anywhere in Gecko via
`MaskConfig::GetString/GetUint32/GetBool`.

**Key techniques**
- **Native getters, not JS shims.** `patches/navigator-spoofing.patch` edits `dom/base/Navigator.cpp`
  so `Navigator::GetUserAgent` consults `NavigatorManager` keyed by `mUserContextId` before the real
  value — per-browser-context personas in C++. The same patch does `dom/workers/WorkerNavigator.cpp`,
  so workers are covered by construction.
- **Timezone in the JS engine.** `patches/timezone-spoofing.patch` reaches `js/src/vm/DateTime.cpp`,
  `js/public/Date.h`, `js/src/vm/Realm.cpp` — not just `Intl`, but the realm's notion of time.
- **Fonts, properly.** `patches/anti-font-fingerprinting.patch` (56 KB) touches
  `gfx/thebes/gfxFont.cpp`, `gfxHarfBuzzShaper.cpp`, `gfxPlatformFontList.cpp`,
  `layout/generic/nsTextFrame.cpp` — seeded per-glyph spacing jitter at the shaping layer, backed by
  **582 real font files** in `bundle/fonts/` (144 Windows, 295 macOS Supplemental, 143 Linux) and a
  generated fontconfig (`pythonlib/camoufox/utils.py:45-75`) so the claimed OS's fonts actually exist.
- **Screen consistency below JS.** `patches/screen-spoofing.patch` hits `gfx/src/nsDeviceContext.cpp`
  and `layout/style/nsMediaFeatures.cpp`, so `matchMedia('(device-width: …)')` agrees with `screen.width`.
- **WebRTC at the peer connection.** `patches/webrtc-ip-spoofing.patch` (42 KB) patches
  `dom/media/webrtc/jsapi/PeerConnectionImpl.cpp`.
- **Un-patching the automation framework.** `patches/playwright/1-leak-fixes.patch` restores
  `Navigator::Webdriver()` to `return false` (upstream Playwright hardcodes `return true`) and removes
  the `PlaywrightPoliciesProvider` enterprise-policy tell — the Firefox analogue of `patch_rebrowser.py`.
- **Config model.** `settings/properties.json` declares **107 typed spoofable properties**: navigator,
  screen, window geometry, `headers.*`, `webrtc:ipv4/localipv4`, `fonts` + `fonts:spacing_seed`,
  `canvas:seed`, `audio:seed`, `webGl:parameters` (+ `blockIfNotDefined`), `voices`, `mediaDevices:*`,
  `geolocation:*`, `timezone`, `locale:*`. Personas are *generated*, not fixed:
  `pythonlib/camoufox/fingerprints.py` draws from `browserforge`, then force-injects OS marker fonts
  (`_MACOS_MARKER_FONTS`, `_WINDOWS_MARKER_FONTS` — CreepJS OS detection) and per-OS essentials into
  every random subset, while `utils.py` guards (`check_custom_fingerprint`, `determine_ua_os`,
  `warn_manual_config`) reject incoherent combos. Contrast `bypasses/hardware_profile.js`: one hardcoded persona.

**What ultrastealth can't do from JS injection.** Sharpest four: CSS media-query agreement
(`nsMediaFeatures.cpp`; `screen_props.js` cannot make `matchMedia` cohere), text metrics from the
shaper (`measureText`/`getBoundingClientRect` widths come from HarfBuzz over fonts that must
physically exist), ICE candidate generation (produced in the network stack before JS observes it),
and worker/OOPIF coverage — a C++ getter has no injection-timing race.

**Adoption cost / licensing.** **MPL-2.0 — file-level copyleft.** Ultrastealth can *depend on*
camoufox freely, but copying a camoufox file into an MIT project keeps that file MPL and obliges
publishing modifications to it. Mostly moot anyway: the patches are Gecko-specific. Ops cost is
real — 1.5 GB tree, a dedicated `docs/patch-upgrading-guide.md`, and `upstream.sh` pinned to a
*beta* (`version=152.0.4 / release=beta.28`). Two caution flags: `upstream.sh:3` now carries
`closedsrc_rev=1.0.0` (a closed component has appeared), and the README states development moved to
`CloverLabsAI/camoufox` and `VulpineOS`, leaving this repo a checkpoint mirror. The bundled fonts
carry their own upstream licenses — a redistribution hazard independent of MPL.

---

## BotBrowser — 2.6k

**Approach.** Mostly documentation. `patches/README.md:5` says it outright: *"The full core remains
proprietary … Only the GUI is open source."* 118 markdown files against **four** illustrative `.diff`
files (`timezone.diff`, `webglAttrs.diff`, `removeHeadless.diff`,
`video_capture_device_descriptor.cc.diff`) plus `args.gn` configs for Chromium **v130–v132** while
the CHANGELOG documents a `151.0.7922.34` release — ~20 majors stale. Personas ship as encrypted
`.enc` blobs (`profiles/stable/chrome149_win11_x64.enc`); `profiles/PROFILE_CONFIGS.md` gates profile
authoring behind "ENT Tier1" and most interesting fields behind Tier2–Tier4.

**Key techniques (from the four published excerpts)**
- A global `BotProfile::Get<T>("dotted.path")` accessor compiled into Chromium — same shape as
  camoufox's `MaskConfig`, keyed off a profile document instead of an env var. Seen in
  `patches/timezone.diff` (overriding `SetIcuTimeZoneAndNotifyV8` in
  `third_party/blink/renderer/core/timezone/timezone_controller.cc`) and `patches/webglAttrs.diff`
  (overriding `WebGLRenderingContextBase::getContextAttributes()`).
- `patches/removeHeadless.diff` deletes `product.insert(0, "Headless")` in
  `components/embedder_support/user_agent_utils.cc`.
- Caveat: `webglAttrs.diff` contains `std::tring` (sic) — hand-edited excerpts for show, not the diff
  they build. Read them as documentation of *ideas*, not code.
- The novel ideas are all prose (`ADVANCED_FEATURES.md:278-360`) and all behavioral: `--bot-time-seed`
  gives each instance a stable *execution-speed* signature across "27 browser operations" plus
  redistributed `performance.getEntries()` values; `--bot-stack-seed` controls V8 recursion depth
  across main thread, Worker, and WASM; Worker threads are pinned via **CPU affinity** so
  parallel-scaling curves match the claimed `navigator.hardwareConcurrency`; plus
  `--bot-js-heap-size-limit`/`--bot-storage-quota` and GPU driver micro-benchmark emulation.

**What ultrastealth can't do from JS injection.** This repo names the category best. Recursion depth
is a pure V8 property with no JS hook. Execution timing is unfakeable by definition — a JS
`getImageData` hook returning doctored pixels still takes the real GPU's time to produce them, so
renderer-string and render-speed disagree. CPU affinity is a syscall.

**Adoption cost / licensing.** `LICENSE` is MIT but covers a repo whose engine isn't in it. Nothing
substantial to copy — the four diffs are small Chromium-derived (BSD-3) hunks. Ideas unencumbered,
artifacts unavailable. Binary is subscription-tiered; Linux alone needs ENT Tier1 (`INSTALLATION.md:113`).

---

## Techniques ultrastealth could adopt

1. **Suppress `--enable-unsafe-swiftshader` in default args** | Playwright forces software WebGL,
   producing a renderer string no real user reports — contradicting whatever `webgl_spoof.js` claims. |
   Likely contributor to `fingerprintscan` 0/1, `infosimples` 14/17. | **S** | None (MIT, `config.py:52`).
2. **Default to isolated-world execution; drop `addBinding`** | `REBROWSER_PATCHES_RUNTIME_FIX_MODE=addBinding`
   still leaves an exposed-function tell; camoufox models the opt-in explicitly as `allowMainWorld`. |
   **rebrowser bot-detector 6/10** — that suite scores `mainWorldExecution`/`exposeFunctionLeak`/`sourceUrlLeak`
   directly. | **M** | None (concept).
3. **Port geoip-driven timezone/locale/geolocation coupling** | Coherence with the proxy exit IP is the
   highest-leverage consistency fix; ultrastealth has no proxy→persona link at all. | `infosimples`,
   `fingerprintscan`. | **S/M** | None — `cloakbrowser/geoip.py` is MIT, port directly.
4. **Font-tell preflight warning** | Warn at launch when the host lacks fonts implied by the claimed OS
   rather than silently emitting a contradiction. | `fingerprintscan`, CreepJS-class. | **S** |
   None — `js/src/fonts.ts` is MIT.
5. **Replace the hardcoded persona with a generated, validated one** | camoufox's `fingerprints.py` +
   `utils.py:244-278` pattern: generate, then *reject* incoherent combos (UA-OS vs `platform` vs fonts
   vs `oscpu`). | `infosimples`, `fingerprintscan`. | **M** | Concept only; don't copy MPL files.
6. **Extend the persona to `matchMedia`/CSS surfaces** | `screen_props.js` fixes `window.screen` but not
   `matchMedia('(device-width:…)')`. Monkey-patching `matchMedia` is partial and itself detectable, but
   closes the crudest mismatch. | `areyouheadless`, `seleniumdetector`. | **M** | None.
7. **Humanized input (Bézier cursor, keystroke cadence)** | Behavioral scoring is a separate axis from
   fingerprinting; both peers ship it behind one flag. | None of the 5 directly — this is what moves
   reCAPTCHA v3 / Turnstile. | **M** | None — `cloakbrowser/human/*.py` is MIT.

## Techniques ultrastealth should NOT adopt

- **Forking and compiling Chromium.** The evidence is in the peers' own repos: CloakBrowser's macOS
  builds sit a full major behind Linux/Windows (`config.py:22-28`); BotBrowser publishes `args.gn` for
  v130–132 while releasing 151; camoufox needs a 1.5 GB tree, 50 patches, and a dedicated rebasing
  guide, and still pins a beta. A ~4-weekly Chromium cadence against a small maintainer is a treadmill
  ending in stale builds — the opposite of ultrastealth's real advantage, which is inheriting Google's
  release train for free.
- **Shipping a browser binary.** ~200 MB × 5 platforms, plus signing, notarization, hosting, and CVE
  liability. It also destroys the persistent-real-profile story in `fetcher.py`.
- **Bundling font files.** camoufox ships 582; the metric-consistency win is real, but the license
  surface (MS core fonts, Apple Supplemental) is a redistribution problem for an MIT repo.
  Detect-and-warn (technique 4) captures most of the value at none of the risk.
- **Copying camoufox source files.** MPL-2.0 would infect them, and they're Gecko-specific anyway.
  Read for technique, cite, reimplement.
- **An encrypted/tiered profile format** (BotBrowser's `.enc`). Wrong for an MIT tool.

---

### Notes and corrections

- The brief describes `hardware_profile.js` as a fixed macOS/M1 persona. It is not:
  `bypasses/hardware_profile.js:1-30` is a **Linux x86_64** persona that *deliberately declines* to
  override `hardwareConcurrency`, with an inline comment noting that `worker_consistency.js` can't
  reliably reach module/cross-origin workers, so a main-thread override would leak as a worker
  mismatch. That comment is exactly the structural limit this document is about — camoufox has no
  such gap because it patches `WorkerNavigator.cpp`. Start any persona rework there.
- Technique→benchmark-failure mappings are **inferred** from test names and documented vectors, not
  measured. Verify with `bot_benchmark.py` before committing effort.
- CloakBrowser's "71 C++ patches" and BotBrowser's full patch tree are **documentation claims with no
  corresponding source in the clones**. Nothing here treats them as confirmed.
