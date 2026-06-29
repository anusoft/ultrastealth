# Cmux Browser Stealth Port Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port the useful, portable cmux/browser-use browser automation and stealth ideas into Ultrastealth, then verify on macOS with tests and bot benchmarks.

**Architecture:** Keep the existing single-process MCP server and `UltrastealthFetcher` launch path. Add cmux-style MCP tools as thin wrappers around the active Playwright page/context, add durable artifact helpers, and tune launch flags without changing the rebrowser patch model.

**Tech Stack:** Python 3.12, `rebrowser-playwright`, FastMCP, unittest, existing `bot_benchmark.py`.

---

### Task 1: Add MCP Regression Tests

**Files:**
- Modify: `tests/test_mcp_profiles.py`
- Modify: `tests/test_fetcher_runner.py`

- [x] **Step 1: Write failing MCP tests**

Add fake page/context coverage for console/error capture, `browser_wait` URL/function/load-state modes, DOM getters/state checks, cookie/storage/session-state tools, and screenshot path saving.

- [x] **Step 2: Run MCP tests and verify RED**

Run: `/Users/mac/.local/pipx/venvs/ultrastealth/bin/python -m unittest tests.test_mcp_profiles -v`

Expected: FAIL because the new MCP tools/helpers do not exist yet.

- [x] **Step 3: Write failing fetcher flag tests**

Add assertions that launch args include browser-use-derived hygiene flags and omit hidden-scrollbar behavior.

- [x] **Step 4: Run fetcher tests and verify RED**

Run: `/Users/mac/.local/pipx/venvs/ultrastealth/bin/python -m unittest tests.test_fetcher_runner -v`

Expected: FAIL on missing/old launch flags.

### Task 2: Implement MCP Port

**Files:**
- Modify: `mcp_server.py`
- Modify: `README.md`

- [x] **Step 1: Add diagnostic and artifact helpers**

Implement page listener attachment for console/page errors and a durable artifact path helper using `/cmux-assets/<branch>/browser/...` with repo-local fallback.

- [x] **Step 2: Add cmux-style MCP tools**

Add `browser_get`, `browser_is`, extended `browser_wait`, `browser_cookies`, `browser_storage`, `browser_state_save`, `browser_state_load`, `browser_console_list`, `browser_console_clear`, `browser_errors_list`, `browser_errors_clear`, `browser_add_init_script`, `browser_add_script`, `browser_add_style`, and optional screenshot path output.

- [x] **Step 3: Run MCP tests and verify GREEN**

Run: `/Users/mac/.local/pipx/venvs/ultrastealth/bin/python -m unittest tests.test_mcp_profiles -v`

Expected: PASS.

### Task 3: Implement Launch Hygiene

**Files:**
- Modify: `fetcher.py`
- Modify: `README.md`

- [x] **Step 1: Update stealth launch flags**

Port browser-use launch hygiene where it improves stealth or macOS operation: visible scrollbars, first-run/search-choice suppression, component-update/domain-reliability consistency, crash-restore suppression, and stable extension/automation feature handling.

- [x] **Step 2: Run fetcher tests and verify GREEN**

Run: `/Users/mac/.local/pipx/venvs/ultrastealth/bin/python -m unittest tests.test_fetcher_runner -v`

Expected: PASS.

### Task 4: Verification, Benchmark, and Integration

**Files:**
- Modify: `CLAUDE.md` if verification notes need updating.

- [x] **Step 1: Run full unit suite**

Run: `/Users/mac/.local/pipx/venvs/ultrastealth/bin/python -m unittest discover -s tests`

Expected: PASS.

- [x] **Step 2: Run focused bot benchmark**

Run: `/Users/mac/.local/pipx/venvs/ultrastealth/bin/python bot_benchmark.py --sites sannysoft rebrowser --results bot_benchmark_results.json`

Expected: Completes and writes JSON. Compare with previous result if available.

- [x] **Step 3: Request code review**

Completed as local diff review plus verification checks; subagent review was not used because this turn did not explicitly delegate subagents.

Use the available review process before committing/merging.

- [ ] **Step 4: Commit and merge to main**

Stage relevant source, docs, and tests. Commit on the feature branch, merge into `main`, and rerun the full unit suite on `main`.
