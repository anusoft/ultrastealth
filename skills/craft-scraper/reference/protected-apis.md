# Protected & token-gated APIs (Cloudflare Turnstile, WAF cookies)

Some targets expose a clean JSON API **but** refuse to serve it to a plain HTTP
client because each data request must carry a token that only a real browser
session can mint. Recognising this early saves you from shipping a scrapling-js
script that always returns zero rows.

## How to recognise it during triage
- The page is a SPA and the data endpoint is visible in the network log, **but**
  you also see calls to `challenges.cloudflare.com/...` (Turnstile/Managed
  Challenge) or a `cf_clearance` cookie, or an F5/BIG-IP `TS...` cookie.
- The app calls a "verify" endpoint (e.g. `/cfturnstile/bypasscloudflare`) and
  the data call only fires after it succeeds.
- Calling the API yourself (curl / scrapling-js) with the captured headers
  returns an empty list or an app-level error code instead of data.

**Worked example — Thai e-GP** (`process5.gprocurement.go.th`): the announcement
grid is served by a JSON API (`egp-atpj27-service/.../a-egp-allt-project`), but
every request is gated by Cloudflare Turnstile. Without a valid token the verify
endpoint reports `bypassCloudflareStatus:"N"` and the search returns error
**E1530 "ค้นหาข้อมูลในฐานข้อมูลไม่พบ"** (no data). See
`examples/egp-announcements.py` for the full Path B script this produced.

## The rule
A plain HTTP client — even scrapling-js with full TLS impersonation — **cannot
solve an interactive Turnstile/Managed Challenge.** TLS impersonation defeats
*passive* fingerprinting; it does not execute the challenge JS or earn a token.
So a Turnstile-gated API is a **Path B (Ultrastealth browser)** target: let the
stealth browser solve the challenge, then read the rendered data (or, if you
captured the minted token + cookies, replay the API from within the same browser
context).

## What makes a Path B script for these targets robust
1. **Persist the browser profile** — pass `user_data_dir` to
   `UltrastealthFetcher`. Cloudflare clearance (`cf_clearance`) is stored in the
   profile and reused on later runs, so you don't re-fight the challenge every
   time.
2. **`solve_cloudflare=True`** on `fetch()`/`fetch_and_evaluate()`, plus a small
   **retry**: wait for a real app element (e.g. the search button); if it's not
   there yet, call `us.solve_cloudflare(page)` again and wait, a few times,
   before giving up.
3. **Wait for the app, not a fixed sleep** — `page.wait_for_function(() => app
   rendered)`; the interstitial + SPA bootstrap can take 10–30 s.
4. **Residential IP.** Cloudflare Turnstile distrusts **datacenter/server IPs**
   and will reject their tokens even from a perfect browser. Run from a
   residential connection (or a residential proxy via `UltrastealthFetcher(proxy=…)`).
   If a script returns 0 rows from a cloud box but works locally, this is why —
   say so in the script header so the next runner isn't confused.
5. **Detect the gate, don't mask it.** Surface the app's error code (e.g. E1530)
   in your output so a token failure reads as a token failure, not "no results".

## Patch the driver
Run `ultrastealth-patch` (or `python -m ultrastealth.patch_rebrowser`) after any
`rebrowser-playwright` (re)install — the unpatched driver leaks
`__pwInitScripts` / `UtilityScript`, which raises your detectability right when
you need stealth most. The fetcher warns at startup if the patch is missing.
