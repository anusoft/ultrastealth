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

## Rules that keep Path B scripts working
- **Extract with `page.evaluate(...)`**, never `page.content()` (can hang on SPAs).
- **Wait for a selector**, never `network_idle=True` (deadlocks the pool).
- **Interact via roles / ARIA / data attributes** and the site's own controls,
  not deep-link `?query=`/`&page=` URLs (params get dropped or A/B-varied).
- **Side-effect-free at import** — the scrape function launches nothing until
  called from `__main__`. Defaults equal the task values.
- On Linux run under `xvfb-run -a python3 <script>.py`.

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
