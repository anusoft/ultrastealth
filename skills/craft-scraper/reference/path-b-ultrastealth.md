# Path B — Ultrastealth headful script

Use only when triage found no usable API, or the task requires real interaction
(form-fill, multi-step flow, clicks). The emitted script uses
**`UltrastealthFetcher`** (rebrowser-playwright + stealth bypasses,
`navigator.webdriver:false`). **Never** plain `playwright`/`selenium` — that
fails bot detection.

## Authoring (code-as-action)

Author with `python3` heredocs that drive the stealth browser, one multi-step
script per Bash call (faster than per-action round-trips). Two ways to drive it
during authoring:

1. **MCP (default, simplest):** explore live with the Ultrastealth MCP
   (`browser_navigate`, `browser_get_state`, `browser_click`, `browser_type`,
   `browser_evaluate`, `browser_screenshot`) to find stable selectors, then bake
   the confirmed flow into the script.
2. **CDP bridge (optional):** start an Ultrastealth browser with
   `ULTRASTEALTH_CDP_PORT=9222` and `connect_over_cdp("http://127.0.0.1:9222")`
   from your heredoc to drive the *same* stealth browser as code. Use only if
   the MCP path is insufficient.

## Emitted script shape (reusable CLI)

```python
import asyncio
from ultrastealth import UltrastealthFetcher

async def scrape_<domain>(arg_a: str, arg_b: int) -> dict:
    """<one-line summary>.

    Args:
        arg_a: <meaning>; <format/allowed>. Default: "<value>".
        arg_b: <meaning>; <range/units>. Default: <value>.
    Returns:
        dict with keys <...>.
    """
    async with UltrastealthFetcher(headless=False) as us:  # headed + Xvfb = stealthiest
        page = await us._context.new_page()
        await page.goto(URL, wait_until="domcontentloaded")
        # interact via get_by_role / aria-label; prefer interactive form-fill
        # over deep-link URLs (params silently dropped, locale/A-B variance).
        # extract via page.evaluate(...) — NOT page.content() (hangs on SPAs).
        ...
        return result

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description=scrape_<domain>.__doc__.splitlines()[0])
    p.add_argument("--arg-a", dest="arg_a", default="<value>", help="...")
    p.add_argument("--arg-b", dest="arg_b", type=int, default=<value>, help="...")
    a = p.parse_args()
    print(asyncio.run(scrape_<domain>(**vars(a))))
```

Rules:
- **Side-effect-free at import** — no browser launch / network / file write at
  module top level. The reusable function must be importable without a run.
- Defaults equal the concrete task values, so a no-arg run reproduces the task.
- Variable values from `plan.md`'s `# Parameters` table → function args + flags.
- Fixed-for-the-site values (start URL, selectors) stay hard-coded.
- Use `page.evaluate(...)` for content extraction; never `page.content()`.
- Never `network_idle=True` waits; never `full_page=True` screenshots.
- On Linux, run headed mode under `xvfb-run -a` (or with `DISPLAY` set).

## Instrumentation & evidence

For each Critical Point, save a screenshot (e.g.
`out/craft/<task_id>/run_<id>/screenshots/cp<N>_<action>.png`) and write a
`step <n> action: <reason>` line to a run log, with the final datum at the end.
Read those PNGs to self-verify — see `reference/verification.md`.

## Run

`python3 <script>.py` (no args reproduces the task), then once with an alternate
`--arg` to prove parameterization. Import-safety smoke test: import the module in
a fresh process and confirm no browser launches.
