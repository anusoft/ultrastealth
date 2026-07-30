#!/usr/bin/env python3
"""<SITE> scraper — <one-line: what it collects>.

Use this path only when there is no usable API (data renders in-DOM) or the task
needs real interaction (clicks, forms, multi-step). It drives a real Chrome under
Ultrastealth (rebrowser-playwright, navigator.webdriver:false), which
passes bot detection where vanilla Playwright/Selenium fails.

Source notes (from Ultrastealth MCP exploration):
  Start URL: <URL>
  Protection: <none | Cloudflare | other>   -> solve_cloudflare below
  Extraction: page.evaluate(...) returning JSON-serialisable data (NOT page.content()).

Usage:
  python3 scraper.py                 # reproduces the original task (defaults below)
  python3 scraper.py --query shoes --pages 3
  python3 scraper.py --help

Bootstrap:
  curl -fsSL https://raw.githubusercontent.com/anusoft/ultrastealth/main/install.sh | bash

Side-effect-free at import: the scrape function is importable without launching a
browser; only the __main__ block runs anything.

On Linux, headed mode needs an X server — run under `xvfb-run -a python3 scraper.py`.
"""
import asyncio
import json
from ultrastealth import UltrastealthFetcher  # install: curl -fsSL https://raw.githubusercontent.com/anusoft/ultrastealth/main/install.sh | bash

# ── Fast alternative: attach to the warm daemon (no cold Chrome start) ──────────
# For a plain navigate → wait → evaluate-extract flow, prefer connecting to the
# always-warm Ultrastealth daemon: the first run starts it once, later runs reuse
# the open browser (startup: seconds → milliseconds) and keep cf_clearance/cookies.
# Use this INSTEAD of UltrastealthFetcher when you don't need a raw Playwright page:
#
#     from ultrastealth import connect
#     async def scrape_fast(query="<default>", pages=1):
#         us = connect()                       # starts daemon once, then reuses
#         await us.call("navigate", url=START_URL, wait_secs=2.0)
#         await us.call("wait", selector="[data-product]", timeout_ms=15000)
#         return (await us.call("evaluate", javascript=EXTRACT))["result"]
#
# Multi-step in one round-trip: await us.call("batch", steps=[{...}, {...}]).
# Keep the UltrastealthFetcher + page_action path below for real interaction
# (multi-step clicks/forms) or residential-IP Turnstile solves.
# ───────────────────────────────────────────────────────────────────────────────

START_URL = "https://site/search"

# JS that runs IN the page and returns plain data. Keep extraction in evaluate
# (the isolated world can read the DOM); never use page.content() — it can hang on
# SPAs. Adjust the selectors to the real markup.
EXTRACT = """
() => [...document.querySelectorAll('[data-product]')].map(el => ({
  title: el.querySelector('.title')?.textContent?.trim() ?? null,
  price: el.querySelector('.price')?.textContent?.trim() ?? null,
  url:   el.querySelector('a')?.href ?? null,
}))
"""


async def scrape_site(query: str = "shoes", pages: int = 1) -> dict:
    """Collect <data> for `query` across `pages` result pages.

    Args:
        query: search term. Default "shoes".
        pages: how many result pages to walk. Default 1.
    Returns:
        {"query", "count", "items": [...]}
    """
    items: list[dict] = []

    # One page_action does the whole multi-step walk on a SINGLE page, so
    # interaction state (search, pagination) is preserved. fetch() navigates to
    # START_URL first, then hands us that page.
    async def walk(page):
        # Prefer the site's own controls over deep-link ?query=/&page= URLs —
        # those params are often dropped, locale-varied, or A/B-tested.
        box = page.get_by_role("searchbox")
        await box.fill(query)
        await box.press("Enter")

        for page_num in range(1, pages + 1):
            # Wait for the result container, NOT network_idle (which can deadlock).
            await page.wait_for_selector("[data-product]", timeout=15000)
            batch = await page.evaluate(EXTRACT)
            items.extend(batch or [])
            print(f"page {page_num}: +{len(batch or [])} (running {len(items)})", flush=True)

            if page_num < pages:
                nxt = page.get_by_role("link", name="Next")
                if await nxt.count() == 0:
                    break
                await nxt.click()

    async with UltrastealthFetcher(headless=False) as us:  # headed + Xvfb = stealthiest
        await us.fetch(
            url=START_URL,
            wait_secs=2.0,
            page_action=walk,
            solve_cloudflare=True,  # harmless if there's no challenge
        )

    return {"query": query, "count": len(items), "items": items}


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description=scrape_site.__doc__.splitlines()[0])
    p.add_argument("--query", default="shoes", help="search term")
    p.add_argument("--pages", type=int, default=1, help="result pages to walk")
    a = p.parse_args()
    result = asyncio.run(scrape_site(query=a.query, pages=a.pages))
    print(json.dumps(result, ensure_ascii=False, indent=2))
