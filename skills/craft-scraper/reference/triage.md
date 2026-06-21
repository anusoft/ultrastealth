# Triage — find the data source (Path A vs Path B)

The single most important step. The fastest, most robust scraper hits a JSON API
directly with **no browser at runtime**. Only fall back to a browser script when
no usable API exists. This decision uses the **Ultrastealth MCP** (stealth
browser — passes bot detection), never vanilla Playwright.

## Procedure

1. **Start capture, then load the page:**
   - `browser_network_enable`
   - `browser_navigate(url)` — for a catalog, navigate to a category/listing
     page so the data XHRs actually fire.
   - Interact if needed (`browser_click`, `browser_type`, `browser_scroll`) to
     trigger lazy-loaded data, then capture again.

2. **List the traffic:**
   - `browser_network_log(filter_type="xhr")` and `("fetch")` — look for
     endpoints returning JSON arrays of the data you want. Note status, size,
     and URL shape.
   - `browser_network_summary` for an overview when there are many requests.

3. **Inspect the candidates:**
   - `browser_network_detail(request_id)` — capture the **request** method,
     query params, and any required headers (`referer`, `authorization`,
     `x-api-key`, cookies, CSRF tokens, locale/store IDs).
   - `browser_network_response_body(request_id)` — confirm the JSON shape and
     where the fields you need live; note pagination (page/offset/cursor) and
     total-count fields.

4. **Decide:**

   | Finding | Path |
   |---|---|
   | A JSON endpoint returns the data (directly callable) | **A** (scrapling-js) |
   | Data is embedded in HTML as JSON (`__NEXT_DATA__`, `window.__DATA__`) | **A** (HTMLRewriter) |
   | No usable API; data only renders in-DOM, or task needs clicks/forms | **B** (Ultrastealth headful) |

## Capture for the emitted script

For Path A, record everything the endpoint needs so the script can call it
headlessly:
- Base URL + path, method, query/body params (the variable ones → CLI flags).
- Required headers — the minimal set that makes the request succeed. `referer`
  is almost always needed; check whether auth/cookies are required or whether
  `stealthyHeaders: true` alone suffices.
- Pagination mechanics and the total-count field (to know when to stop).
- Any one-time token fetched by an earlier request (capture that request too).

## Notes

- Many sites work with just `stealthyHeaders: true` + a `referer`. Try the
  minimal header set first; add captured cookies/tokens only if the bare request
  is rejected.
- If the API rejects headless HTTP even with captured headers/cookies (rare),
  that's a signal to use Path B.
- Use `browser_status` / `browser_cleanup` on long triage sessions to free
  memory.
