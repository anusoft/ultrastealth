# Path A — scrapling-js (Bun) scraper

Preferred path. The emitted script hits the discovered JSON API (or parses
embedded JSON) directly with **no browser at runtime**.

## Setup

```bash
bun add scrapling-js
```

Docs: https://anusoft.github.io/scrapling-js/

## Required conventions

- **Runtime:** Bun only (`bun run <script>.js`). Never npm/node/yarn.
- **HTTP client:** scrapling-js `Fetcher` only:
  ```js
  import { Fetcher } from "scrapling-js";
  ```
  Never `fetch`/`axios`/`node-fetch`.
- **HTML parsing:** Bun's `HTMLRewriter` (for embedded JSON / light HTML), or the
  scrapling-js `Selector` API for CSS/XPath. Avoid jsdom/DOMParser.
- **Output:** informative, not verbose (count + 1–2 sample rows per section,
  page progress, final summary). Write data files under `out/` (or a
  task-appropriate dir).

## `Fetcher` surface

```js
// GET with stealthy headers + query params
const r = await Fetcher.get(`${API_BASE}/path`, {
  params: { page: "1", store: "1000" },
  headers: { accept: "application/json", referer: "https://site/" },
  stealthyHeaders: true,                       // on by default
});
if (!r.ok) { console.error(`HTTP ${r.status}`); process.exit(1); }
const data = JSON.parse(r.body);

// POST JSON
const p = await Fetcher.post(`${API_BASE}/path`, {
  headers: { accept: "application/json", "content-type": "application/json",
             referer: "https://site/" },
  json: { ... },
});

// CSS / XPath selectors when you must parse HTML
const page = await Fetcher.get("https://site/list");
const titles = page.css("h3.title::text").getAll();
const hrefs  = page.css("a.card::attr(href)").getAll();
```

Embedded JSON (SPA with no API but data in the page):
```js
let content = "";
await new HTMLRewriter()
  .on("script#__NEXT_DATA__", { text(c) { content += c.text; } })
  .transform(new Response(html)).text();
const data = JSON.parse(content);
```

> Note: Bun's `HTMLRewriter` does NOT decode HTML entities in attributes —
> `getAttribute("href")` returns literal `&amp;`. Decode before regex-matching.

## Script structure (required)

1. **Config block** — `API_BASE`, `OUT_DIR`, file paths, captured headers.
2. **CLI** — parse `process.argv`; support `--resume` (skip already-downloaded
   files) and `--help` (usage). Variable parameters from `plan.md` become flags
   with the concrete task values as defaults.
3. **Discovery** — fetch the category/index dynamically (don't hardcode the
   tree) when the target paginates by category.
4. **Download loop** — walk categories / paginate until empty; save JSON per
   page; print progress.
5. **Summary** — totals, files written, disk usage, elapsed time.

Keep the script **side-effect-free at import** — wrap work in `run()`/`main()`
and call it at the bottom.

## Verify

Run it (`bun run <script>.js`, and once with `--resume`), then assert against the
Critical Points: expected row/section counts (vs the API total-count field),
required fields present in saved JSON, params reflected in results, and `--resume`
skipping existing files. See `reference/verification.md`.
