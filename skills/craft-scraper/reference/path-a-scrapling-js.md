# Path A — scrapling-js (Bun) scraper

Preferred path. The emitted script calls the discovered JSON API (or parses
embedded JSON) directly with **no browser at runtime**. Start from
`templates/scraper.scrapling-js.js` and adapt it — it already encodes the quality
bar; you mostly fill in the captured endpoint and the response shape.

## Setup
```bash
curl -fsSL https://raw.githubusercontent.com/anusoft/scrapling-js/main/install.sh | bash
```
This bootstraps the current script directory as a Bun project and installs
`scrapling-js` from GitHub. In an existing Bun project, run
`bun add github:anusoft/scrapling-js` until the npm package is published.

Docs: https://anusoft.github.io/scrapling-js/

## What to change in the template
1. **Config block** — `API_BASE`, `ENDPOINT`, `REFERER`, `PAGE_SIZE`, and the
   minimal `HEADERS` you captured in triage.
2. **Total + items fields** — set `total = data.total ?? …` and
   `items = data.items ?? …` to the *actual* JSON paths from
   `browser_network_response_body`. These two lines are the most common cause of
   a zero-row run; verify them against a real response.
3. **Params → flags** — every value from `plan.md`'s Parameters table becomes a
   flag with the task's concrete value as default (the template shows the argv
   pattern).
4. **Pagination** — page-based is in the template; for offset/cursor, adjust the
   loop and the stop condition accordingly.

## Key `Fetcher` facts
- `Fetcher.get(url, { params, headers, stealthyHeaders: true, timeout, retries, retryDelay })`
  — `stealthyHeaders` defaults true; `retries` (3) and `retryDelay` (1000ms) are
  built in, so don't hand-roll retry loops.
- `Fetcher.post(url, { json: {...} })` for JSON bodies; `{ data: {...} }` for
  form-encoded.
- The `Response` has `.ok`, `.status`, `.body` (string), `.headers`, `.cookies`,
  and the selector API (`.css(...)`, `.xpath(...)`). There is **no `.json()`** —
  use `JSON.parse(r.body)`.

## Passive Cloudflare / WAF 403s
If a JSON route works in the Ultrastealth browser but plain `Fetcher` gets a
Cloudflare 403 with no Turnstile, no Managed Challenge, and no browser-minted
token/cookie requirement, keep Path A and use the TLS-impersonated transport:
import `generateChromeHeaders` from `scrapling-js`, import `fetch` from
`wreq-js`, then call `wreq-js` with `browser: chrome_<version>`, the returned
`os`, and the generated headers. Verify the exact endpoint returns 2xx before
generating the crawler. If the route needs an interactive token such as
`cf_clearance`, switch to Path B.

## Selectors (only when you must parse HTML, not an API)
```js
const page = await Fetcher.get(url);
const titles = page.css("h3.title::text").getAll();
const hrefs  = page.css("a.card::attr(href)").getAll();
```

## Embedded JSON (SPA with data baked into the page)
```js
let buf = "";
await new HTMLRewriter()
  .on("script#__NEXT_DATA__", { text(c) { buf += c.text; } })
  .transform(new Response(html)).text();
const data = JSON.parse(buf);
```
> Bun's `HTMLRewriter` does NOT decode HTML entities in attributes —
> `getAttribute("href")` returns literal `&amp;`. Decode before regex-matching.

## Verify
Run `bun run <script>.js`, then once with `--resume`. Assert row/section counts
against the API's total-count field, confirm required fields are present in the
saved JSON, and that `--resume` skips existing files. See
`reference/verification.md`.
