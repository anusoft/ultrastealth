#!/usr/bin/env bun
/**
 * <SITE> scraper — <one-line: what it collects>.
 *
 * Data source (reverse-engineered with the Ultrastealth MCP):
 *   <METHOD> <ENDPOINT>            e.g. GET https://site/api/products?page=N
 *   Auth/headers required: <referer only | cookie X | bearer | none>
 *   Pagination: <page|offset|cursor>; total-count field: <path.to.total>
 *
 * Runtime: Bun only.  No browser at runtime — pure HTTP via scrapling-js.
 * Bootstrap this script directory:
 *   curl -fsSL https://raw.githubusercontent.com/anusoft/scrapling-js/main/install.sh | bash
 *
 * Usage:
 *   bun run scraper.js                 # reproduces the original task (defaults below)
 *   bun run scraper.js --limit 3       # first 3 pages only
 *   bun run scraper.js --resume        # skip pages already saved
 *   bun run scraper.js --out ./data    # change output dir
 *   bun run scraper.js --help
 *
 * This file is side-effect-free at import: nothing runs unless invoked directly
 * (`import.meta.main`), so it can be imported and unit-tested.
 */

import { Fetcher } from "scrapling-js"; // install: curl -fsSL https://raw.githubusercontent.com/anusoft/scrapling-js/main/install.sh | bash
import { mkdir, readdir, writeFile } from "node:fs/promises";
import { join } from "node:path";

// ── Config (fixed-for-the-site values live here, not as flags) ───────────────
const API_BASE = "https://site/api";
const ENDPOINT = "/products";
const REFERER = "https://site/";
const PAGE_SIZE = 50; // server's page size, for the total→pages calc
const HEADERS = { accept: "application/json", referer: REFERER };

// ── CLI (every value the task could vary becomes a flag; defaults = the task) ─
function parseArgs(argv) {
  const a = { resume: false, limit: Infinity, delayMs: 400, out: "out", startPage: 1 };
  for (let i = 0; i < argv.length; i++) {
    const v = argv[i];
    if (v === "--help" || v === "-h") { printHelp(); process.exit(0); }
    else if (v === "--resume") a.resume = true;
    else if (v === "--limit") a.limit = Number(argv[++i]);
    else if (v === "--delay") a.delayMs = Number(argv[++i]);
    else if (v === "--out") a.out = argv[++i];
    else if (v === "--start") a.startPage = Number(argv[++i]);
    else { console.error(`unknown arg: ${v}`); printHelp(); process.exit(1); }
  }
  return a;
}
function printHelp() {
  console.log(`Usage: bun run scraper.js [--resume] [--limit N] [--start N] [--delay MS] [--out DIR]`);
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/**
 * One JSON request with status checking. scrapling-js retries transient
 * failures internally (retries/retryDelay); we only add a clear error on a
 * non-2xx so the loop fails loud instead of writing garbage.
 */
async function getJSON(path, params) {
  const r = await Fetcher.get(`${API_BASE}${path}`, {
    params,
    headers: HEADERS,
    stealthyHeaders: true,
    timeout: 30000,
    retries: 3,
    retryDelay: 1000,
  });
  if (!r.ok) throw new Error(`HTTP ${r.status} for ${path} ${JSON.stringify(params)}`);
  try {
    return JSON.parse(r.body);
  } catch {
    throw new Error(`Non-JSON response (${r.status}) for ${path} — first 200 chars: ${r.body.slice(0, 200)}`);
  }
}

async function run(opts) {
  const t0 = Date.now();
  await mkdir(opts.out, { recursive: true });
  const existing = opts.resume ? new Set(await readdir(opts.out)) : new Set();

  // Probe page 1 to learn the total (so we know when to stop, not just "until empty").
  const first = await getJSON(ENDPOINT, { page: String(opts.startPage) });
  const total = first?.total ?? first?.count ?? null; // <-- adjust to the real field
  const lastPage = total ? Math.min(Math.ceil(total / PAGE_SIZE), opts.startPage + opts.limit - 1)
                         : opts.startPage + opts.limit - 1;
  console.log(`total≈${total ?? "unknown"} → pages ${opts.startPage}..${Number.isFinite(lastPage) ? lastPage : "until-empty"}`);

  let rows = 0, pages = 0, skipped = 0;
  for (let page = opts.startPage; page <= lastPage; page++) {
    const fname = `page-${String(page).padStart(4, "0")}.json`;
    if (existing.has(fname)) { skipped++; continue; }

    const data = page === opts.startPage ? first : await getJSON(ENDPOINT, { page: String(page) });
    const items = data?.items ?? data?.results ?? data?.data ?? []; // <-- adjust to real shape
    if (items.length === 0) { console.log(`page ${page}: empty — stopping`); break; }

    await writeFile(join(opts.out, fname), JSON.stringify(items, null, 2));
    rows += items.length; pages++;
    if (pages <= 1) console.log(`  sample:`, JSON.stringify(items[0]).slice(0, 160));
    console.log(`page ${page}: +${items.length} (running ${rows})`);

    if (page !== opts.startPage) await sleep(opts.delayMs); // be polite
  }

  const secs = ((Date.now() - t0) / 1000).toFixed(1);
  console.log(`\nDone: ${rows} rows across ${pages} pages (${skipped} skipped) → ${opts.out}/ in ${secs}s`);
  return { rows, pages, skipped };
}

if (import.meta.main) {
  run(parseArgs(process.argv.slice(2))).catch((e) => { console.error(e.message); process.exit(1); });
}

export { run, getJSON };
