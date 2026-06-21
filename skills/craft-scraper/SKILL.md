---
name: craft-scraper
description: Use when the user wants to scrape a website or automate a web task and wants a REUSABLE script as the artifact (not a one-shot scrape result). Drives a one-time discovery loop with the Ultrastealth MCP, then emits a deterministic, parameterized script that reruns forever with no LLM — a fast scrapling-js (Bun, HTTP) script when the site has a usable JSON API, or an Ultrastealth (Python, headed real-Chrome) script for protected/JS-rendered sites. Triggers on "scrape X", "build a scraper for", "make a reusable script that pulls…", "download all products/listings from", "automate this web task and save it as a script". Prefer this whenever the user wants something they can run again, even if they don't say the word "scraper".
allowed-tools: Bash, Read, Write, Edit, Grep, Glob, mcp__ultrastealth__browser_navigate, mcp__ultrastealth__browser_evaluate, mcp__ultrastealth__browser_get_html, mcp__ultrastealth__browser_get_state, mcp__ultrastealth__browser_screenshot, mcp__ultrastealth__browser_network_enable, mcp__ultrastealth__browser_network_log, mcp__ultrastealth__browser_network_detail, mcp__ultrastealth__browser_network_response_body, mcp__ultrastealth__browser_network_summary, mcp__ultrastealth__browser_click, mcp__ultrastealth__browser_type, mcp__ultrastealth__browser_scroll
---

# Craft-Scraper

Run an LLM-driven discovery loop **once** and emit a **reusable, deterministic
script** the user reruns forever **without any LLM**. The browser and the agent
are disposable; the script is the artifact. Optimize for a script that still
works next month, with different arguments, run unattended.

Two pillars decide everything:

1. **Discover with stealth.** Inspect the target with the **Ultrastealth MCP**
   (real Chrome, passes bot detection, `navigator.webdriver:false`). Never drive
   vanilla Playwright/Selenium — it fails bot checks and is project-banned.
2. **Emit the right kind of script.** Most sites expose a JSON API; the fastest,
   most robust artifact calls it directly with **scrapling-js (Bun)** and uses
   **no browser at runtime**. Fall back to an **Ultrastealth headed Python**
   script only when there is no usable API or the task needs real interaction.

> Prerequisite: the Ultrastealth MCP must be connected — see
> https://anusoft.github.io/ultrastealth/#install . If MCP tools aren't
> available, say so and stop; discovery depends on them.

## The loop

Track each step (a TODO per step) and do them in order. One action per step;
read the output before the next.

### 1. Plan
Write `out/craft/<task_id>/plan.md` with a `# Parameters` table (every value the
user could vary → a CLI flag whose **default is the concrete task value**, so a
no-arg run reproduces the task) and a `# Critical Points` checklist (each
constraint / required datum, independently verifiable). Contract in
`reference/verification.md`.

### 2. Triage — find the data source (the highest-leverage step)
Follow `reference/triage.md`: `browser_network_enable` → `browser_navigate` →
trigger the data (scroll/click/type) → `browser_network_log(filter_type="xhr")`
→ inspect candidates with `browser_network_detail` /
`browser_network_response_body`. Capture the endpoint, method, params, the
minimal headers/cookies/tokens it needs, the pagination mechanism, and the
total-count field.

- **JSON API, or embedded JSON (`__NEXT_DATA__`, `window.__DATA__`)** → **Path A**.
- **No usable API; data only in the rendered DOM, or task needs clicks/forms** → **Path B**.
- **API exists but is gated by Cloudflare Turnstile / a Managed Challenge** (you
  see `challenges.cloudflare.com`, a `cf_clearance`/`TS…` cookie, or the API
  returns an error/empty unless a browser-minted token is present) → **Path B**.
  A plain HTTP client can't mint that token — read `reference/protected-apis.md`.

### 3. Author from the template
Copy the matching template and adapt it — don't write from scratch. The
templates already encode the quality bar (retries, pagination, resume,
import-safety, polite pacing).

- **Path A** → start from `templates/scraper.scrapling-js.js`; details in
  `reference/path-a-scrapling-js.md`.
- **Path B** → start from `templates/scraper.ultrastealth.py`; details in
  `reference/path-b-ultrastealth.md`.

### 4. Execute
Run the script once with defaults (reproduces the task), then once with an
altered argument to prove parameterization. For Path A, also run with `--resume`
to prove it skips finished work.

### 5. Self-verify
Walk every Critical Point per `reference/verification.md` — assert on the emitted
JSON/row counts (Path A) or read the saved screenshots (Path B). Be harsh on
empty or suspiciously short results. Diagnose → fix → re-run on any failure.

### 6. Deliver
Only when every CP is evidenced. Propose the destination path, **confirm it with
the user**, write the script there, then show the user `--help` and the final
datum/row count so they know how to rerun it.

## The quality bar (what makes a generated script "good")

A script that scrapes once in the chat is not the deliverable. Every emitted
script must clear this bar — the templates already do, so preserve these
properties as you adapt them:

- **Reproducible by default.** No args → reproduces the exact task. Each varying
  value is a flag whose default is the task's concrete value.
- **Side-effect-free at import.** All work lives in a function called only under
  `import.meta.main` (JS) / `if __name__ == "__main__"` (Py). Importing the file
  must not fetch, launch a browser, or write files.
- **Fails loud, not silent.** Check HTTP status; raise on non-2xx and on
  non-JSON-when-JSON-expected. A wrong selector or expired token should error,
  not silently write zero rows.
- **Knows when to stop.** Use the total-count field to bound pagination; treat an
  empty page as a stop signal too. Never loop forever.
- **Resumable for big jobs.** When it writes many files, support `--resume` to
  skip ones already on disk.
- **Polite.** A small delay between requests; rely on scrapling-js's built-in
  retries/backoff rather than hammering.
- **Honest output.** Print progress (page, running total), one sample row, and a
  final summary (rows, files, elapsed). The user should be able to trust the run
  from stdout alone.
- **Robust selectors/fields.** Prefer stable hooks (data attributes, API field
  names, ARIA roles) over brittle nth-child chains and layout classes.

## Hard rules

- **Never install or drive Playwright/Selenium directly.** scrapling-js for HTTP
  (Path A), `UltrastealthFetcher` for browser (Path B).
- **Always use Bun** to run JS/TS (`bun run …`), never npm/node/yarn.
- Use **`python3`** for Python. On Linux, Path B headed mode runs under
  `xvfb-run -a`.
- Prefer the discovered API over scraping rendered HTML. Prefer the site's own
  controls (form-fill, Next button) over brittle deep-link `?query=`/`&page=`
  URLs.

## Files
- `templates/scraper.scrapling-js.js` — gold-standard Path A starting point.
- `templates/scraper.ultrastealth.py` — gold-standard Path B starting point.
- `examples/egp-announcements.py` — real worked Path B example (a Cloudflare-
  Turnstile-gated government API; persistent profile, solve-retry, E-code handling).
- `reference/triage.md` — API-first vs browser decision via MCP network capture.
- `reference/path-a-scrapling-js.md` — adapt the scrapling-js template.
- `reference/path-b-ultrastealth.md` — adapt the Ultrastealth template.
- `reference/protected-apis.md` — Turnstile/WAF-gated APIs: why they're Path B,
  persistent profile, residential-IP requirement.
- `reference/verification.md` — `plan.md` contract + how to verify.
