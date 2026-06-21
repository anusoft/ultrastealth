---
description: Author a reusable, deterministic scraper/automation script from a prompt + URL (stealth, API-first).
argument-hint: <what to scrape/automate, with the target URL and any concrete values>
---

You are operating as the **craft-scraper** agent. First read the `SKILL.md` next
to this `commands/` folder and the relevant `reference/*.md` files, then author a
reusable script for this task:

$ARGUMENTS

Run the loop from `SKILL.md`:

1. **Plan** — `out/craft/<task_id>/plan.md` with a `# Parameters` table
   (defaults = the concrete values in the task above, so a no-arg run reproduces
   the task) and a `# Critical Points` checklist. See `reference/verification.md`.

2. **Triage** — use the Ultrastealth MCP network-capture flow in
   `reference/triage.md` to find the JSON API (Path A) or confirm a browser is
   required (Path B). Capture any headers/cookies/tokens the endpoint needs.

3. **Author** —
   - Path A: a Bun scrapling-js scraper per `reference/path-a-scrapling-js.md`
     (no runtime browser, `Fetcher`, `stealthyHeaders`, `--resume`/`--help`).
   - Path B: an Ultrastealth headful Python script per
     `reference/path-b-ultrastealth.md` (one reusable function + argparse,
     import-safe).

4. **Execute** the script once and capture output.

5. **Self-verify** every Critical Point per `reference/verification.md`.
   Diagnose → fix → re-run on failure.

6. **Deliver** — propose the destination path, confirm with the user, write the
   script there, then show `--help` and the final datum/row count.

Never drive Playwright/Selenium directly; use scrapling-js (HTTP) or
`UltrastealthFetcher` (browser). Always run JS with Bun and Python with
`python3`.
