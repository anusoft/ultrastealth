# Prior art: HTTP/TLS impersonation & the Scrapling upstream

**Survey date:** 2026-07-30
**Sources read:** `/Users/mac/Projects/_prior-art/curl_cffi` (lexiforest/curl_cffi, v0.16.0b2, MIT, last commit 2026-07-26), `/Users/mac/Projects/_prior-art/Scrapling` (D4Vinci/Scrapling, v0.4.12, **BSD 3-Clause** — *not* MIT, last commit 2026-07-27), `/Users/mac/Projects/scrapling-js` (MIT), `wreq-js@2.3.1` (MIT).

> **Thesis.** scrapling-js's TLS *engine* is not behind curl_cffi — on raw profile currency `wreq-js@2.3.1` is **ahead** (Chrome 147 vs 146, Edge 147 vs 101, Firefox 149 vs 147, Safari 26.2 vs 26.0.1). The problem is that the shipped library never calls it. `src/fetcher.ts` — the only thing `src/index.ts` exports as `Fetcher`/`FetcherSession` — uses the runtime's plain `fetch()`; `wreq-js` appears **only** in `stealth-proxy.ts`, `bench_fetch_wreq.ts`, and `benchmark.ts`. Every request through the public API therefore ships a Chrome User-Agent over Bun/Node/Workers TLS: the exact JA3-vs-UA contradiction the header module exists to prevent. Fix the wiring before touching profile versions. Separately, the header generator emits a confirmed coherence contradiction (`Referer` + `Sec-Fetch-Site: none`) that is currently asserted by a test.

## TLS impersonation: capability comparison

| Capability / signal | curl_cffi 0.16.0b2 | wreq-js 2.3.1 | Gap for scrapling-js |
|---|---|---|---|
| Newest Chrome profile | `chrome146` | `chrome_147` | wreq ahead |
| Newest Edge | `edge101` (**2022-era**) | `edge_147` | wreq far ahead |
| Newest Firefox / Safari | `firefox147` / `safari2601` | `firefox_149` / `safari_26.2` | wreq ahead |
| Mobile / other clients | `chrome131_android`, iOS Safari, `tor145` | iOS + iPad Safari, Firefox Android, Opera 116–130, OkHttp 3.9–5 | wreq broader |
| Cipher list, curves, sigalgs | ✅ `_apply_fingerprint` | ✅ `cipherList` / `curvesList` / `sigalgsList` | parity |
| TLS extension **order** + permutation | ✅ `TLS_EXTENSION_ORDER`, `SSL_PERMUTE_EXTENSIONS` | ✅ `extensionPermutation`, `permuteExtensions` | parity |
| GREASE · ALPS (+ new codepoint) · ECH | ✅ `TLS_GREASE`, `SSL_ENABLE_ALPS`, `TLS_USE_NEW_ALPS_CODEPOINT`, `ECH` | ✅ `greaseEnabled`, `alpsProtocols`, `alpsUseNewCodepoint`, `enableEchGrease` | parity |
| Certificate compression | ✅ zlib/brotli | ✅ zlib/brotli/**zstd** | wreq ahead |
| Post-quantum key share (X25519MLKEM768) | ✅ in `TLS_EC_CURVES_MAP` (id 4588) | via `curvesList` string | parity (inferred) |
| Record size limit · session ticket · PSK | ✅ | ✅ (+ `pskDheKe`, `pskSkipSessionTicket`) | wreq ahead |
| HTTP/2 SETTINGS values · pseudo-header order · priority | ✅ `HTTP2_SETTINGS`, `HTTP2_PSEUDO_HEADERS_ORDER`, `STREAM_WEIGHT` | ✅ typed fields, `headersPseudoOrder`, `priorities[]` | parity |
| HTTP/2 SETTINGS **frame order** | ⚠️ implied by the akamai string | ✅ explicit `settingsOrder[]` | wreq ahead |
| Regular-header order | ✅ `HTTPHEADER_ORDER` | ✅ `emulation.origHeaders` | **not used by scrapling-js** |
| **HTTP/3 / QUIC fingerprint** | ✅ QUIC transport params, H3 SETTINGS + pseudo-header order (`chrome145/146`, `firefox147`) | ❌ ALPN selects HTTP3, no H3 *fingerprint* controls | **curl_cffi only** |
| WebSocket handshake fingerprint · commercial fingerprint feed | ✅ `WS_HTTPHEADER_ORDER`; impersonate.pro | partial; ❌ | curl_cffi ahead |

## curl_cffi — impersonation targets & mechanism

Targets are enumerated in `/Users/mac/Projects/_prior-art/curl_cffi/curl_cffi/requests/impersonate.py` (`BrowserTypeLiteral`, 37 concrete targets) and described with OS metadata in `curl_cffi/fingerprints.py` (`NATIVE_IMPERSONATE_TARGETS`). Defaults resolve through `resolve_latest_browser_type()`: `chrome` → `chrome146`, `firefox` → `firefox147`, `safari` → `safari2601`, `edge` → `edge101`.

Two mechanisms exist. **Native targets** call `curl_easy_impersonate()` in the lexiforest curl-impersonate fork (`curl_cffi/curl.py:539`), where the profile lives in C. **Data-driven fingerprints** — the `Fingerprint` dataclass at `curl_cffi/fingerprints.py:358` — carry ~40 fields (TLS, HTTP/2, HTTP/3, WebSocket, *and* `headers` + `header_order`) applied field-by-field by `_apply_fingerprint()` (`curl_cffi/requests/utils.py:419`). Fingerprints can also be hand-written as JA3/Akamai strings: `set_ja3_options()` (`utils.py:256`) splits the 5-field JA3 into cipher list, extension order and curves; `set_akamai_options()` (`utils.py:299`) splits `settings|window_update|streams|header_order` into the corresponding HTTP/2 curlopts; `set_perk_options()` does the HTTP/3 equivalent. Session API adds `RetryStrategy(count, delay, jitter, backoff)` and a pluggable HAR-format response cache (`curl_cffi/requests/cache.py`).

**Coherence model:** the impersonate target owns *both* the TLS stack and the default header block, and default headers never override user-supplied ones (`utils.py:546-560`). curl_cffi additionally *warns* when `ja3`/`akamai`/`extra_fp` are mutated after `impersonate` is set (`utils.py:941-985`) — an explicit anti-incoherence guard. Its candidly documented weakness: every stock UA is macOS (`docs/impersonate/faq.rst:81`).

## Is scrapling-js's TLS layer current?

**Engine: current, arguably best-in-class. Wiring: absent. Coherence: leaking.**

- *Confirmed* — `wreq-js@2.3.1` exposes 125 profiles through `chrome_147` / `edge_147` / `firefox_149` / `safari_26.2` (`node_modules/wreq-js/dist/wreq-js.d.ts:12`). That beats curl_cffi on every desktop family.
- *Confirmed* — `src/headers.ts` caps `CHROME_VERSIONS` at `[145, 146, 147]`, correctly matching the ceiling. The comment claiming a cap at 147 is right; the task brief's "chrome_145–148" is not — 148 does not exist in this build.
- *Confirmed* — `src/fetcher.ts` never imports wreq-js. `makeRequest()` calls the global `fetch()`. So `Fetcher.get()` sends Chrome headers over the runtime's own TLS: mismatched JA3/JA4, mismatched HTTP/2 Akamai hash, mismatched header order.
- *Confirmed mismatch class #1* — `buildHeaders()` (`src/fetcher.ts:88`) sets `Sec-Fetch-Site: none` and *then* adds `Referer: https://www.google.com/search?q=<domain>`. Real Chrome sends `none` only for typed/bookmark navigations, which have **no** Referer; a Google referer implies `cross-site`. `tests/utils.test.ts:131` asserts the broken combination. Upstream Scrapling avoids this by using a bare `https://www.google.com/` referer and letting browserforge own the Sec-Fetch block (`scrapling/engines/static.py:167`).
- *Confirmed mismatch class #2* — on the `stealth-proxy.ts` path, `generateChromeHeaders()` output is passed as `headers`, **overriding** wreq's own profile-coherent default header set. Randomising the Not-A-Brand token and pinning brand order to `Chrome, Not-A-Brand, Chromium` (real Chrome permutes the three) fights the engine instead of trusting it.
- *Confirmed* — `generateHeaders()` emits Firefox or Edge UAs 20% of the time, for which no TLS profile is ever selected.
- *Confirmed* — `benchmark.ts` only checks a JA4 *exists* and starts with `t13d`; it never compares against a real Chrome 147 JA4/Akamai hash, so profile drift would still score 100%.
- *Structural:* Cloudflare Workers cannot load wreq-js (native Rust addon), so a Workers deployment is permanently the plain-`fetch` tier. Say so in the README rather than implying TLS impersonation everywhere.

## Scrapling upstream: HTTP-layer features not in scrapling-js

*(excludes the already-fixed session-proxy and quoted-charset issues)*

1. **Impersonation owns the headers** — `scrapling/engines/static.py:166` (`_headers_job`). When `impersonate` is on, Scrapling generates **no** headers, only a referer; curl supplies the coherent set. Port value: **high** (removes mismatch class #2). Effort: **S**.
2. **browserforge header generation** — `scrapling/engines/toolbelt/fingerprints.py:37`. A Bayesian generator trained on real captures produces coherent header sets *including order*, vs scrapling-js's hand-rolled pools. No Worker-safe JS equivalent exists. Effort: **L** (or **S** to vendor a static table of real captures).
3. **`follow_redirects: "safe"`** — default at `static.py:82`; rejects redirects into private/internal IPs (SSRF guard). scrapling-js follows all redirects. Port value: high (security). Effort: **S**.
4. **Retry only on transport errors** — `static.py:222-262` retries `CurlError` and rotates the proxy on `is_proxy_error`; it does **not** retry a returned 4xx/5xx. scrapling-js retries any thrown error with a flat delay, no jitter or backoff. Effort: **S**.
5. **`impersonate` as a list** — `_select_random_browser()` (`static.py:35`) picks a random profile per request from a caller-supplied pool. scrapling-js exposes no profile knob at all. Effort: **S**.
6. **Adaptive relocation** — `Selector.relocate()` (`scrapling/parser.py:517`), `__calculate_similarity_score()` (`parser.py:805`), `element_to_dict()` (`scrapling/core/utils/_utils.py:84`), SQLite persistence (`scrapling/core/storage.py`). Fingerprints tag, cleaned attributes, text, ancestor-tag path, parent attrs/text and sibling tags; scores every candidate with `difflib.SequenceMatcher`, normalises to a percentage, accepts ≥40%. Re-finds an element after a site redesign. **Highest-value port — signature feature, pure DOM logic, no Python-only deps.** Effort: **M**.
7. **`find_similar()`** — `parser.py:1013`. Given one product card, XPath-selects same-depth/same-ancestor-chain siblings and attribute-matches them. Effort: **S–M**.
8. **Spider layer** — `scrapling/spiders/` (new in 0.4.x): `CrawlerEngine`, `AutoThrottle` with `Retry-After` parsing (`throttle.py:10`), disk `ResponseCacheManager` (`cache.py`), `CheckpointManager`, `LinkExtractor`, robots.txt. Effort: **L**.
9. **Async session lifecycle** — `_ASyncSessionLogic`/`AsyncFetcherClient` (`static.py:414+`) with `async with` and connection reuse; scrapling-js's `FetcherSession` has neither. Effort: **M**.
10. **XPath** — first-class upstream; scrapling-js's README advertises it but `src/selector.ts` implements none (cheerio is CSS-only). Effort: **S** (fix doc) / **M** (implement).

## Opportunity for ultrastealth: an impersonated-HTTP fast path?

**Honest verdict: low value as a stealth play, moderate value as a latency play, and only if it stays small.**

Ultrastealth has *no* HTTP client — `pyproject.toml` declares none, and the only `urllib` use is `bot_benchmark.py:35`. Its TLS is real Chrome's, which is why `peetws` and `browserleaks` score 100%. An impersonated-HTTP path can only *approximate* what it already does perfectly: it adds a weaker tier, not a stronger one.

Where it could pay is latency. The daemon owns a warm Chrome and every op costs a CDP round trip; fetching 200 product JSON endpoints after login is browser-overhead-bound, not network-bound. Adding an `http_get` op to the `OPS` registry (`browser_core.py:311` — a plain dict) is genuinely a few lines plus a `curl_cffi` dependency.

Cookie/UA coherence is achievable: rebrowser-playwright's `context.cookies()` returns the full jar including HttpOnly, and `navigator.userAgent` is one `evaluate` away; feed both into `curl_cffi.requests.Session(impersonate="chrome146", headers={"User-Agent": <captured>})`. But note the seam — the real Chrome here is 148 and curl_cffi's newest is 146, so the fast path's JA3 would *not* match the JA3 that origin already saw during the browser navigation. For a site correlating TLS fingerprint against session cookie, that is a **new** detection surface the browser-only path does not have.

Recommendation: do not build it speculatively. Build it only when a concrete crawl is measured as browser-overhead-bound, scope it to same-origin XHR/JSON after a browser-established session, and keep it opt-in per call.

## Recommended actions

### SCRAPLING-JS

1. **Wire wreq-js into `Fetcher`/`FetcherSession`** as the default transport, falling back to global `fetch()` when the native addon is unavailable (Workers). The headline feature is currently dead code; every public-API request leaks a stock-runtime JA3. **M.**
2. **Fix the Referer / `Sec-Fetch-Site` contradiction** in `src/headers.ts` — emit `cross-site` alongside a Referer, or drop the Referer and keep `none`. Update `tests/utils.test.ts:131`. **S.**
3. **Stop overriding wreq's profile headers.** Pass only `Referer`/`Accept-Language`/caller headers and let `browser`+`os` supply the rest, per Scrapling's `_headers_job`. **S.**
4. **Tie browser choice to profile choice** — wreq-js has `firefox_149`/`edge_147`, so a Firefox UA can get a Firefox TLS profile instead of none. **S.**
5. **Harden `benchmark.ts`**: assert the JA4 and Akamai H2 hash *equal* a pinned real-Chrome value rather than merely existing, so profile drift fails the build. **S.**
6. **Add `follow_redirects: "safe"`** plus retry jitter/backoff and an explicit `retryStatuses`. **S.**
7. **Port `relocate()` + `find_similar()`.** ⚠️ Scrapling is **BSD 3-Clause**, not MIT — a port must carry the BSD notice and Karim Shoair's copyright, or be a clean reimplementation from the algorithm above. **M.**
8. **Correct the XPath claim** in `README.md`, or implement it. **S.**

### ULTRASTEALTH

9. **No action on TLS.** Real Chrome already wins and both benchmark sites pass 100%; an impersonation library would only add a weaker tier. **n/a.**
10. **Defer the `http_get` fast path** until a crawl is measured as browser-overhead-bound. If built: register it in `browser_core.py`'s `OPS` dict, seed from `context.cookies()` + the live UA, restrict to same-origin post-navigation requests, and document that its JA3 will not match the browser's. **M, low priority.**
11. **Expose a `cookies` op** in `browser_core.py` regardless — useful for handoff to scrapling-js and for session export, independent of any fast path. **S.**

### Licenses

curl_cffi and wreq-js are **MIT** — free to borrow from. **Scrapling is BSD 3-Clause** (Karim Shoair, 2024), not MIT as the brief assumed: any port of its code must carry the BSD notice and copyright. Both ultrastealth and scrapling-js are MIT, which accepts MIT and BSD-3 inbound.
