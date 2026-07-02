# Task
Test the installed craft-scraper skill against https://www.watsons.co.th/ and produce a reusable Scrapling JS crawler if Watsons exposes a usable API. The proof run is intentionally bounded so the one-shot test finishes quickly while still exercising category discovery, product listing, product details, pricing, variants/options, and reviews when exposed by the site.

# Parameters
| name | type | source phrase from task | default | allowed / format |
|------|------|-------------------------|---------|------------------|
| start-url | str | "https://www.watsons.co.th/" | "https://www.watsons.co.th/" | absolute Watsons URL |
| out | str | "Test ... craft-scraper" | "out/watsons-products" | output directory |
| category-limit | int | "one-shot" bounded proof | 2 | positive integer; increase for wider crawl |
| page-limit | int | "one-shot" bounded proof | 1 | positive integer; increase for more listing pages per category |
| product-limit | int | "one-shot" bounded proof | 3 | positive integer; increase for more products per category |
| review-page-limit | int | "review" proof path | 1 | positive integer |
| review-limit | int | "review" bounded proof | 50 | positive integer or "all"; caps saved reviews per product because Watsons can ignore pageSize |
| delay | int | "craft crawling scripts" polite pacing | 100 | milliseconds between product/detail calls |
| resume | bool | "reusable script" | false | flag only |

# Critical Points
- [x] CP1: Use the craft-scraper Path A flow: discover a Watsons JSON source with Ultrastealth, then run Scrapling JS/Bun with no browser at runtime.
- [x] CP2: Discover multiple Watsons categories from live site data, not hard-coded examples.
- [x] CP3: Fetch listing data for at least one category and save product URLs/SKUs from that category.
- [x] CP4: Fetch product detail data for at least one product, including name, brand when available, pricing, images, and product URL.
- [x] CP5: Capture variants/options or explicitly record that the product has none in the source response.
- [x] CP6: Attempt reviews through the discovered site source and save review summary/items when available, or record an explicit no-reviews result from the API.
- [x] CP7: Script supports --help, is side-effect-free at import, and --resume skips already saved product details.
- [x] CP8: The local curl+bash Scrapling JS installer path works for the generated script, or any blocking installer issue is identified concretely.

# Verification Evidence
- MCP caveat: Codex MCP calls to `browser_network_enable` and `browser_restart` failed with `Transport closed`; discovery used the working Ultrastealth CLI browser instead. The emitted runtime script uses no browser.
- Watsons blocks plain curl with Akamai `403`, but Scrapling JS `generateChromeHeaders` plus `wreq-js` returned `200` for `/api/v2/wtcth/products/search`, product detail, and reviews.
- `bun run ./watsons.scrapling-js.js --help` passed and shows the two curl+bash setup commands plus all flags.
- Import safety passed: importing `watsons.scrapling-js.js` exported `discoverCategories`, `fetchListing`, `fetchProductDetail`, `fetchReviewPage`, `flattenCategoryHierarchy`, `normalizeProduct`, `parseArgs`, and `run` with no fetch/write side effects.
- No-arg bounded run passed: discovered 13 live categories, selected 2, fetched 2 category pages, wrote 6 product detail files, and saved 247 review items from 4534 available review items.
- Altered-args run passed: `--category-limit 1 --product-limit 1 --review-limit 5` wrote 1 product and 5 review items.
- Resume run passed: reused 2 category pages, fetched 0 product details, skipped 6 existing product files.
- Data assertions passed on `out/watsons-products-verify`: category count 13, category `010000` listing total 2523 with 20 products on page 1, 6 product files, sample `BP_288766` has URL, numeric THB price, 4 images, 1 variant, and 50 saved reviews from 1060 available reviews.
- Product field audit passed on `out/watsons-products-field-audit`: 4 product files all had brand, Watsons URL, numeric THB price, grouped pricing, images, variant price/stock data, description text, specification arrays, and capped review items. Sample brands included Vaseline, L'Oreal, and Glad2Glow.
- Local installer smoke passed in a temp directory using `/Users/mac/Projects/scrapling-js/install.sh` with `SCRAPLING_JS_PACKAGE=file:/Users/mac/Projects/scrapling-js`; the generated script ran with `SCRAPLING_JS_LOCAL=/tmp/no-such-scrapling-js`.
- Public installer blocker: anonymous `curl -I` to both `https://raw.githubusercontent.com/anusoft/ultrastealth/main/install.sh` and `https://raw.githubusercontent.com/anusoft/scrapling-js/main/install.sh` returned `HTTP/2 404`.
