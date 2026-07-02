# Path B — Ultrastealth headed Python script

Use only when triage found no usable API, or the task needs real interaction
(form-fill, clicks, multi-step). Start from `templates/scraper.ultrastealth.py`
and adapt it. The script drives a real Chrome via `UltrastealthFetcher`
(rebrowser-playwright + bypasses, `navigator.webdriver:false`) — never plain
`playwright`/`selenium`.

## Use the high-level API — it keeps one page across the whole flow

The most common mistake is reaching into `us._context.new_page()` per page, or
calling `fetch_and_evaluate` in a loop (each call opens a **new** page, so
clicking "Next" loses state). Instead pass a single **`page_action`** that does
the entire multi-step walk on one page — the template shows this:

```python
async def walk(page):              # page is already at START_URL
    box = page.get_by_role("searchbox"); await box.fill(query); await box.press("Enter")
    for n in range(1, pages + 1):
        await page.wait_for_selector("[data-product]", timeout=15000)
        items.extend(await page.evaluate(EXTRACT))     # extract via evaluate
        if n < pages and await page.get_by_role("link", name="Next").count():
            await page.get_by_role("link", name="Next").click()

async with UltrastealthFetcher(headless=False) as us:
    await us.fetch(url=START_URL, wait_secs=2.0, page_action=walk, solve_cloudflare=True)
```

Methods you'll use:
- `fetch(url, wait_secs, page_action, solve_cloudflare)` → navigates, runs your
  `page_action(page)` on that page, returns HTML. Best for interactive flows.
- `fetch_and_evaluate(url, js_expression, page_action, wait_secs, solve_cloudflare)`
  → same, but returns the result of one final `page.evaluate`. Best for a single
  page with no pagination.
- `solve_cloudflare=True` is harmless when there's no challenge — leave it on for
  protected targets.

## Fast path — attach to the warm daemon (no cold start)

For the common **navigate → wait → evaluate-extract** flow, attach to the warm
Ultrastealth daemon instead of cold-launching a browser every run. The first run
starts the daemon (once); every later run reuses the already-open Chrome, so
startup drops from seconds to milliseconds:

```python
from ultrastealth import connect

us = connect()                       # starts the daemon once, then reuses it
await us.call("navigate", url=START_URL, wait_secs=2.0)
await us.call("wait", selector="[data-product]", timeout_ms=15000)
rows = (await us.call("evaluate", javascript=EXTRACT))["result"]
```

Persistent `cf_clearance`/cookies live in the daemon's profile, so protected
targets stay solved across runs. Multi-step flows can go in one round-trip via
`await us.call("batch", steps=[...])`. Use this whenever you do **not** need a raw
Playwright `page` object.

Keep the `UltrastealthFetcher` + `page_action` path (above) only when the task
needs real interaction on a single live page (multi-step clicks/forms) that the
RPC ops can't express, or a residential-IP Turnstile solve.

## Rules that keep Path B scripts working
- **Extract with `page.evaluate(...)`**, never `page.content()` (can hang on SPAs).
- **Wait for a selector**, never `network_idle=True` (deadlocks the pool).
- **Interact via roles / ARIA / data attributes** and the site's own controls,
  not deep-link `?query=`/`&page=` URLs (params get dropped or A/B-varied).
- **Side-effect-free at import** — the scrape function launches nothing until
  called from `__main__`. Defaults equal the task values.
- On Linux run under `xvfb-run -a python3 <script>.py`.

## Cloudflare Turnstile / token-gated APIs
If triage showed the target is behind Cloudflare Turnstile or a Managed Challenge
(or an API that returns empty/an error code without a browser-minted token), read
`reference/protected-apis.md` and study `examples/egp-announcements.py` — it shows
the robust pattern: persistent `user_data_dir`, `solve_cloudflare=True` with a
wait-for-app retry, header-mapped table extraction, and surfacing the app's error
code. These targets also need a **residential IP** — Turnstile rejects datacenter
IPs.

## Discovery during authoring
Explore live with the MCP (`browser_navigate`, `browser_get_state`,
`browser_click`, `browser_type`, `browser_evaluate`, `browser_screenshot`) to
find stable selectors and confirm the flow, then bake the confirmed steps into
the `page_action`.

## Evidence for verification
For each Critical Point, save a screenshot
(`out/craft/<task_id>/run_<id>/screenshots/cp<N>.png`) inside the `page_action`
(`await page.screenshot(path=...)`) and read it back to verify. See
`reference/verification.md`.
