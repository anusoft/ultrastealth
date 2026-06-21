#!/usr/bin/env python3
"""Thai e-GP procurement announcements scraper (process5.gprocurement.go.th).

Collects the procurement-announcement listing from the e-GP "All Web" portal
(egp-agpc01-web), the page at:
  https://process5.gprocurement.go.th/egp-agpc01-web/announcement?announcementTodayFlag=true

WHY THIS IS A BROWSER (Path B) SCRIPT, NOT AN HTTP (scrapling-js) ONE
--------------------------------------------------------------------
Triage with the Ultrastealth MCP showed the page is an Angular micro-frontend
that loads its data from a JSON API (egp-atpj27-service / a-egp-allt-project).
BUT every data request is gated by a Cloudflare **Turnstile** token (the app
calls `/api/v1/cfturnstile/bypasscloudflare` and the backend returns error
**E1530 "ค้นหาข้อมูลในฐานข้อมูลไม่พบ"** when the token is missing/invalid —
`bypassCloudflareStatus: "N"`). A plain HTTP client (even with TLS impersonation)
cannot mint an interactive Turnstile token, so the robust artifact drives a real
stealth Chrome, lets it solve Cloudflare, runs the search, and reads the rendered
results table.

Usage:
  python3 scrape_egp_announcements.py                 # today's announcements (default)
  python3 scrape_egp_announcements.py --no-today      # all (not just today)
  python3 scrape_egp_announcements.py --pages 5       # walk up to 5 result pages
  python3 scrape_egp_announcements.py --out ./out.json
  python3 scrape_egp_announcements.py --help

On Linux run headed under Xvfb:  xvfb-run -a python3 scrape_egp_announcements.py
Side-effect-free at import: nothing runs until __main__.
"""
import asyncio
import json
from ultrastealth import UltrastealthFetcher

BASE = "https://process5.gprocurement.go.th/egp-agpc01-web/announcement"

# Runs IN the page. Finds the results table (the one beneath the
# "จำนวนโครงการที่พบ : N โครงการ" counter) and maps each row's cells to its column
# header, so the output is labelled and resilient to column re-ordering. Returns
# {count, error, rows}. `count` is the portal's own "found" number; `error` is any
# E-code message shown instead of data (e.g. E1530 = none found / token rejected).
EXTRACT = r"""
() => {
  const body = document.body.innerText || "";
  const m = body.match(/จำนวนโครงการที่พบ\s*:\s*([\d,]+)/);
  const count = m ? Number(m[1].replace(/,/g, "")) : null;
  const err = (body.match(/E\d{3,5}\s*:\s*[^\n]+/) || [null])[0];

  // Pick the table with the most data rows (the results grid).
  const tables = [...document.querySelectorAll("table")];
  let best = null, bestRows = -1;
  for (const t of tables) {
    const n = t.querySelectorAll("tbody tr").length;
    if (n > bestRows) { best = t; bestRows = n; }
  }
  if (!best) return { count, error: err, rows: [] };

  const headers = [...best.querySelectorAll("thead th, thead td")].map(h => (h.innerText || "").trim());
  const rows = [...best.querySelectorAll("tbody tr")].map(tr => {
    const cells = [...tr.querySelectorAll("td")].map(td => (td.innerText || "").trim().replace(/\s+/g, " "));
    if (cells.length <= 1) return null;                 // skip "no data" placeholder rows
    const obj = {};
    cells.forEach((c, i) => { obj[headers[i] || `col${i}`] = c; });
    const link = tr.querySelector("a[href]");
    if (link) obj._href = link.href;
    return obj;
  }).filter(Boolean);

  return { count, error: err, rows };
}
"""


async def scrape_egp_announcements(today: bool = True, pages: int = 3,
                                   profile_dir: str | None = None) -> dict:
    """Scrape e-GP procurement announcements.

    Args:
        today: only today's announcements (announcementTodayFlag). Default True.
        pages: max result pages to walk via the ถัดไป (Next) pager. Default 3.
        profile_dir: persist the Chrome profile here so Cloudflare clearance is
            reused across runs (strongly recommended for this CF-gated site).
            Default: a stable dir next to this script.
    Returns:
        {"today", "found", "count", "items": [...]}  (count = portal's reported total)
    """
    import os
    url = f"{BASE}?announcementTodayFlag={'true' if today else 'false'}"
    if profile_dir is None:
        profile_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".egp-profile")
    items: list[dict] = []
    reported = None

    # JS predicate: the Angular app has finished bootstrapping when the ค้นหา
    # (Search) button exists. Used to wait past the Cloudflare interstitial.
    HAS_SEARCH = """() => [...document.querySelectorAll('button,a,[role=button]')]
        .some(e => (e.innerText||'').trim() === 'ค้นหา')"""

    async def walk(page):
        nonlocal reported
        # The portal sits behind Cloudflare; fetch() already ran solve_cloudflare,
        # but the managed challenge + Angular bootstrap can take a while. Wait for
        # the app to actually render, re-solving if an interstitial is still up.
        for attempt in range(3):
            try:
                await page.wait_for_function(HAS_SEARCH, timeout=30000)
                break
            except Exception:
                await us.solve_cloudflare(page)        # noqa: F821 (us in closure)
                await page.wait_for_timeout(3000)
        else:
            print("app did not render (Cloudflare interstitial not cleared) — "
                  "retry from a residential IP / real Chrome profile", flush=True)
            return

        # Trigger the search the page would otherwise wait for a user to click.
        await page.evaluate("""() => {
            const b = [...document.querySelectorAll('button,a,[role=button]')]
              .find(e => (e.innerText||'').trim() === 'ค้นหา');
            if (b) b.click();
        }""")

        seen_keys = set()
        for page_num in range(1, pages + 1):
            # Wait for either real rows or the "no data / error" state to settle.
            await page.wait_for_timeout(1500)
            data = await page.evaluate(EXTRACT)
            reported = data.get("count")
            fresh = 0
            for r in data.get("rows", []):
                key = r.get("ชื่อโครงการ", "") + "|" + r.get("_href", "") + "|" + str(r)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                items.append(r)
                fresh += 1
            note = f" [{data['error']}]" if data.get("error") else ""
            print(f"page {page_num}: +{fresh} rows (reported total={reported}){note}", flush=True)

            if fresh == 0:
                break  # error/no-data or no new rows -> stop

            # Paginate via the site's own Next control; stop if absent/disabled.
            clicked = await page.evaluate("""() => {
                const b = [...document.querySelectorAll('button,a,[role=button]')]
                  .find(e => (e.innerText||'').trim() === 'ถัดไป');
                if (!b || b.disabled || b.getAttribute('aria-disabled') === 'true') return false;
                b.click(); return true;
            }""")
            if not clicked:
                break

    async with UltrastealthFetcher(headless=False, user_data_dir=profile_dir) as us:  # headed + persistent profile
        await us.fetch(url=url, wait_secs=4.0, page_action=walk, solve_cloudflare=True)

    return {"today": today, "found": reported, "count": len(items), "items": items}


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="Scrape Thai e-GP procurement announcements.")
    p.add_argument("--today", dest="today", action="store_true", default=True,
                   help="only today's announcements (default)")
    p.add_argument("--no-today", dest="today", action="store_false",
                   help="all announcements, not just today")
    p.add_argument("--pages", type=int, default=3, help="max result pages to walk (default 3)")
    p.add_argument("--profile", default=None, help="persistent Chrome profile dir (reuses CF clearance)")
    p.add_argument("--out", default=None, help="write JSON to this file (default: stdout)")
    a = p.parse_args()

    result = asyncio.run(scrape_egp_announcements(today=a.today, pages=a.pages, profile_dir=a.profile))
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"\nWrote {result['count']} rows (portal reported {result['found']}) → {a.out}")
    else:
        print(text)
