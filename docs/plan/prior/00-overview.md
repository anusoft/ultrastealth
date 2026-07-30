# Prior-art survey: the stealth browser automation landscape

**Survey date:** 2026-07-30
**Filters applied:** ≥1,000 GitHub stars **and** last commit on the default branch ≥ 2026-04-30 (within 3 months).
**Method:** curated candidate list verified against `GET /repos/{repo}` and `GET /repos/{repo}/commits?per_page=1`, cross-checked with `gh search repos --topic=…` sweeps over `anti-detection`, `antibot`, `bot-detection`, `browser-fingerprinting`, `web-scraping`, `browser-automation`, `stealth`, `fingerprinting`, `undetected`. Star counts and dates below are as-verified on the survey date, not from README claims.

The 16 highest-value repos are shallow-cloned to `/Users/mac/Projects/_prior-art/` (1.9 GB; `camoufox` alone is 1.5 GB because it vendors a Firefox source tree). That directory is outside the ultrastealth repo and is not tracked by git.

---

## Headline finding

> **Ultrastealth's core stealth dependency is the stalest artifact in the entire surveyed landscape.**

`rebrowser/rebrowser-patches` — which supplies the `rebrowser-playwright` fork that [`fetcher.py`](../../../fetcher.py) is built on — last shipped a commit on **2025-05-09, roughly 15 months ago**. Every other meaningful project in this space has moved in the last six weeks. Its actively-maintained functional equivalent, `Kaliiiiiiiiii-Vinyzu/patchright` (3,945★, last commit 2026-07-15) and its Python sibling `patchright-python` (1,446★, 2026-07-16), are current.

This is not an abstract concern. Ultrastealth's own benchmark scores **6/10 on `bot-detector.rebrowser.net`** — the detection suite written by the very project whose patches it depends on. That gap is the most legible symptom of the staleness. See [`02-playwright-patching.md`](02-playwright-patching.md) for the migration assessment.

A secondary structural observation: the field has split into two schools, and ultrastealth sits in the older one.

- **Injection school** (ultrastealth, patchright, rebrowser, nodriver): drive a stock browser, hide the automation artifacts from JavaScript-land.
- **Patched-binary school** (CloakBrowser, camoufox, BotBrowser): ship a modified browser where the fingerprint is authentic at the C++ level and there is nothing to hide.

The patched-binary school is where the star growth is (CloakBrowser at 29k, camoufox at 10.6k). It also carries a large, permanent maintenance cost — tracking upstream Chromium/Firefox releases forever. [`01-stealth-engines.md`](01-stealth-engines.md) evaluates whether any of it is borrowable without adopting that burden.

---

## Repos that passed both filters

### Direct stealth peers
| Repo | ★ | Last commit | What it is | Cloned |
|---|---:|---|---|:-:|
| [CloakHQ/CloakBrowser](https://github.com/CloakHQ/CloakBrowser) | 29,382 | 2026-07-30 | Stealth Chromium, drop-in Playwright replacement | ✓ |
| [daijro/camoufox](https://github.com/daijro/camoufox) | 10,619 | 2026-07-19 | Firefox anti-detect; fingerprints injected at C++ level | ✓ |
| [pinchtab/pinchtab](https://github.com/pinchtab/pinchtab) | 9,781 | 2026-07-29 | Automation bridge + multi-instance orchestrator w/ stealth | ✓ |
| [jo-inc/camofox-browser](https://github.com/jo-inc/camofox-browser) | 8,186 | 2026-07-20 | Stealth headless browser for AI agents | — |
| [autoscrape-labs/pydoll](https://github.com/autoscrape-labs/pydoll) | 6,984 | 2026-07-24 | Chromium automation with no WebDriver | ✓ |
| [ultrafunkamsterdam/nodriver](https://github.com/ultrafunkamsterdam/nodriver) | 4,578 | 2026-05-13 | Raw-CDP successor to undetected-chromedriver | ✓ |
| [Kaliiiiiiiiii-Vinyzu/patchright](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright) | 3,945 | 2026-07-15 | Undetected Playwright (Node) | ✓ |
| [botswin/BotBrowser](https://github.com/botswin/BotBrowser) | 2,560 | 2026-07-29 | Patched Chromium core, unified fingerprint defense | ✓ |
| [Kaliiiiiiiiii-Vinyzu/patchright-python](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright-python) | 1,446 | 2026-07-16 | **Undetected Playwright (Python) — closest drop-in** | ✓ |
| [cdpdriver/zendriver](https://github.com/cdpdriver/zendriver) | 1,376 | 2026-07-15 | Actively-maintained nodriver fork | ✓ |

### Frameworks with stealth modes
| Repo | ★ | Last commit | Relevance | Cloned |
|---|---:|---|---|:-:|
| [seleniumbase/SeleniumBase](https://github.com/seleniumbase/SeleniumBase) | 12,903 | 2026-07-26 | UC Mode / CDP Mode; disconnect-reconnect evasion | ✓ |
| [g1879/DrissionPage](https://github.com/g1879/DrissionPage) | 12,312 | 2026-07-22 | Python automation, hybrid HTTP+browser session model | — |
| [omkarcloud/botasaurus](https://github.com/omkarcloud/botasaurus) | 5,611 | 2026-07-26 | Scraper framework with anti-detect defaults | — |

### Fingerprint & TLS layer
| Repo | ★ | Last commit | Relevance | Cloned |
|---|---:|---|---|:-:|
| [fingerprintjs/fingerprintjs](https://github.com/fingerprintjs/fingerprintjs) | 27,975 | 2026-07-21 | **Adversary reference** — what detection actually measures | ✓ |
| [lexiforest/curl_cffi](https://github.com/lexiforest/curl_cffi) | 6,182 | 2026-07-26 | Maintained curl-impersonate binding; TLS SOTA | ✓ |
| [niespodd/browser-fingerprinting](https://github.com/niespodd/browser-fingerprinting) | 5,115 | 2026-07-27 | Written analysis of Cloudflare/Akamai/PerimeterX/Kasada | ✓ |
| [apify/fingerprint-suite](https://github.com/apify/fingerprint-suite) | 2,530 | 2026-07-24 | Statistically-consistent fingerprint generation + injection | ✓ |
| [bogdanfinn/tls-client](https://github.com/bogdanfinn/tls-client) | 1,759 | 2026-07-02 | Go TLS-fingerprint client | — |

### Challenge-specific
| Repo | ★ | Last commit | Relevance | Cloned |
|---|---:|---|---|:-:|
| [FlareSolverr/FlareSolverr](https://github.com/FlareSolverr/FlareSolverr) | 14,946 | 2026-07-16 | Cloudflare-challenge proxy service | — |
| [sarperavci/CloudflareBypassForScraping](https://github.com/sarperavci/CloudflareBypassForScraping) | 2,509 | 2026-07-17 | Turnstile/CF bypass technique reference | — |

### Engine & platform peers
| Repo | ★ | Last commit | Relevance | Cloned |
|---|---:|---|---|:-:|
| [browser-use/browser-use](https://github.com/browser-use/browser-use) | 107,264 | 2026-07-27 | Agent tool-surface reference (ultrastealth's MCP tools are modeled on it) | — |
| [microsoft/playwright](https://github.com/microsoft/playwright) | 93,689 | 2026-07-29 | Upstream baseline for the patch layer | — |
| [unclecode/crawl4ai](https://github.com/unclecode/crawl4ai) | 75,575 | 2026-07-15 | LLM-oriented extraction output formats | — |
| [D4Vinci/Scrapling](https://github.com/D4Vinci/Scrapling) | 71,776 | 2026-07-27 | Upstream of sibling project `scrapling-js` | ✓ |
| [lightpanda-io/browser](https://github.com/lightpanda-io/browser) | 32,986 | 2026-07-30 | Lightweight engine — already in `bot_benchmark.py` | — |
| [apify/crawlee](https://github.com/apify/crawlee) | 25,101 | 2026-07-29 | Session pooling & retry patterns | — |
| [browserbase/stagehand](https://github.com/browserbase/stagehand) | 23,676 | 2026-07-28 | Agent SDK ergonomics | — |
| [h4ckf0r0day/obscura](https://github.com/h4ckf0r0day/obscura) | 19,820 | 2026-07-29 | Rust engine — already in `bot_benchmark.py` | — |
| [apify/crawlee-python](https://github.com/apify/crawlee-python) | 9,376 | 2026-07-30 | Python port of the above | — |
| [steel-dev/steel-browser](https://github.com/steel-dev/steel-browser) | 7,399 | 2026-07-20 | Session-managed browser API for agents | ✓ |
| [go-rod/rod](https://github.com/go-rod/rod) | 7,046 | 2026-07-15 | Go CDP driver design | — |

---

## Repos excluded, and why it matters

Exclusions are as informative as inclusions here — several are things ultrastealth or its peers still depend on.

| Repo | ★ | Last commit | Stale by | Note |
|---|---:|---|---|---|
| **rebrowser/rebrowser-patches** | 1,403 | 2025-05-09 | **~15 mo** | **Ultrastealth's own stealth dependency.** See headline finding. |
| ultrafunkamsterdam/undetected-chromedriver | 12,774 | 2025-07-05 | ~13 mo | Superseded by the author's own `nodriver`. |
| berstend/puppeteer-extra | 7,385 | 2023-03-01 | ~3.4 yr | `puppeteer-extra-plugin-stealth` is effectively abandoned. |
| lwthiker/curl-impersonate | 6,720 | 2024-03-03 | ~2.4 yr | Superseded by `lexiforest/curl_cffi`, which is included. |
| VeNoMouS/cloudscraper | 6,673 | 2025-06-10 | ~14 mo | — |
| daijro/browserforge | 1,192 | 2026-02-26 | ~5 mo | Narrowly missed; its successor role is largely filled by `fingerprint-suite`. |
| ttlns/Selenium-Driverless | 860 | 2024-11-23 | ~20 mo | Also fails the star threshold. |

Three of ultrastealth's conceptual ancestors (`undetected-chromedriver`, `puppeteer-extra-plugin-stealth`, `rebrowser-patches`) are all stale. The techniques survived; the packages did not. Anything ultrastealth still inherits from them should be treated as unmaintained code it now effectively owns.

---

## How to read the rest of this directory

| File | Question it answers |
|---|---|
| [`01-stealth-engines.md`](01-stealth-engines.md) | What can a patched browser binary do that JS injection structurally cannot? Is any of it borrowable? |
| [`02-playwright-patching.md`](02-playwright-patching.md) | Should ultrastealth migrate off the stale `rebrowser-playwright` to `patchright`? Leak-vector coverage matrix. |
| [`03-cdp-drivers.md`](03-cdp-drivers.md) | Is the driverless raw-CDP approach worth adopting as an alternate runner? |
| [`04-fingerprint-layer.md`](04-fingerprint-layer.md) | Signal-by-signal gap analysis of the 13 `bypasses/*.js` vs what detectors measure; diagnoses of known benchmark failures. |
| [`05-http-tls.md`](05-http-tls.md) | TLS impersonation SOTA; feature delta for the sibling `scrapling-js` project. |
| [`06-session-architecture.md`](06-session-architecture.md) | What the warm daemon needs to become a multi-session platform. |
| [`07-roadmap.md`](07-roadmap.md) | **The synthesized, prioritized plan.** Start here if you want the actions rather than the evidence. |
