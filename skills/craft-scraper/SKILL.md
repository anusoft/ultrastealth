---
name: craft-scraper
description: Use when the user wants to scrape a site or automate a web task and wants a REUSABLE script as the artifact (not a one-shot answer). Drives a one-time authoring loop — discover the site's data source via the Ultrastealth MCP, then emit a deterministic script that reruns forever without any LLM. Triggers on "scrape X", "build a scraper for", "make a reusable script that", "download all products from", "automate this web task and save it".
allowed-tools: Bash, Read, Write, Edit, Grep, Glob, mcp__ultrastealth__browser_navigate, mcp__ultrastealth__browser_evaluate, mcp__ultrastealth__browser_get_html, mcp__ultrastealth__browser_screenshot, mcp__ultrastealth__browser_network_enable, mcp__ultrastealth__browser_network_log, mcp__ultrastealth__browser_network_detail, mcp__ultrastealth__browser_network_response_body, mcp__ultrastealth__browser_network_summary, mcp__ultrastealth__browser_click, mcp__ultrastealth__browser_type, mcp__ultrastealth__browser_get_state
---

# Craft-Scraper

Run an LLM-driven authoring loop **once** and emit a **reusable, deterministic
script** the user can rerun forever **without any LLM**. The browser/agent is
disposable; the script is the artifact.

Two pillars:

1. **Stealth discovery.** Inspect the target with the **Ultrastealth MCP**
   (real Chrome, passes bot detection, `navigator.webdriver:false`). Never drive
   vanilla Playwright/Selenium.
2. **Right tool for the target.** Most sites expose a JSON API — the fastest,
   most robust artifact calls it directly with **scrapling-js (Bun)** and uses
   **no browser at runtime**. Only fall back to a browser script (Ultrastealth
   headful) when there is no usable API or the task needs real interaction.

> Prerequisite: the **Ultrastealth MCP** must be connected. See
> https://anusoft.github.io/ultrastealth/#install

## Modes

- **`/craft-scraper <task + url>`** — author a reusable, parameterized script.
- Also activates from any prompt matching the description above.

## The loop

Track each step (a TODO item per step) and work them in order.

### 1. Plan
Write `out/craft/<task_id>/plan.md` with a `# Parameters` table (every value the
user could vary → becomes a CLI flag whose default is the concrete task value)
and a `# Critical Points` checklist (every constraint / required datum,
independently verifiable). See `reference/verification.md`.

### 2. Triage — find the data source
Use the MCP to decide Path A vs Path B per `reference/triage.md`:
`browser_network_enable` → `browser_navigate` → `browser_network_log`
(xhr/fetch) → inspect promising endpoints with `browser_network_detail` /
`browser_network_response_body`. Capture any required headers/cookies/tokens.

- **JSON API found (or usable embedded JSON like `__NEXT_DATA__`)** → **Path A**.
- **SPA with no usable API, or the task requires interaction** → **Path B**.

### 3. Author the script
- **Path A** → `reference/path-a-scrapling-js.md`: a Bun scrapling-js script, no
  runtime browser.
- **Path B** → `reference/path-b-ultrastealth.md`: an Ultrastealth headful
  Python script.

### 4. Execute
Run the emitted script once and capture stdout/stderr.

### 5. Self-verify
Walk every Critical Point per `reference/verification.md` (read PNGs for Path B,
assert on emitted JSON/row counts for Path A). Diagnose → fix → re-run on any
failure.

### 6. Deliver
Only when every CP is evidenced. Propose the destination path, **confirm it with
the user**, write the script there, then show the user `--help` and the final
datum/row count.

## Hard rules

- **Never install or drive Playwright/Selenium directly.** scrapling-js for HTTP
  (Path A), `UltrastealthFetcher` for browser (Path B).
- **Always use Bun** to run JS/TS (`bun run ...`), never npm/node/yarn.
- Use **`python3`** for Python.
- One action per step; observe output before the next.
- Prefer the discovered API over scraping rendered HTML. Prefer interactive
  form-filling over brittle deep-link URLs when a browser path must parameterize
  a search.
- The reusable script must be **side-effect-free at import** and support
  `--help` (and `--resume` where it downloads many files).

## Reference files
- `reference/triage.md` — API-first vs browser decision via MCP network capture.
- `reference/path-a-scrapling-js.md` — emit a Bun scrapling-js scraper.
- `reference/path-b-ultrastealth.md` — emit an Ultrastealth headful script.
- `reference/verification.md` — `plan.md` contract + verification.
