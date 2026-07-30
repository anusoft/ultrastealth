# Prior art: session management, pooling & agent-facing API design

> Thesis: ultrastealth's warm daemon is a good primitive; here's what it's missing to be a platform.

**Read date:** 2026-07-30. All claims below are from reading source in `/Users/mac/Projects/_prior-art/`. Inferences are marked *(inferred)*.

**Licenses:** steel-browser **Apache-2.0** (`steel-browser/LICENSE` — permissive, MIT-compatible, but carries attribution/NOTICE and patent-grant terms MIT does not); pinchtab **MIT** (`pinchtab/LICENSE`, © Luigi Agosti); SeleniumBase **MIT** (`SeleniumBase/LICENSE`). No AGPL or commercial-restricted peer in this cohort.

## Capability comparison

| Capability | ultrastealth today | steel-browser | pinchtab | SeleniumBase |
|---|---|---|---|---|
| Concurrent browser sessions | **1** (module globals in `browser_core.py`) | **1 live** + history (`activeSession` is singular) | **N** child processes (`orchestrator.instances` map) | N/A — per-process driver, no server |
| Op concurrency | serialized by one `asyncio.Lock` (`daemon.py:50`) | per-request, single browser | per-instance; optional queue w/ fairness | caller's problem |
| Per-session proxy | no | yes — own `ProxyServer` per session, tx/rx accounted | yes — per-instance/profile | `proxy_helper.py` per driver |
| Per-session profile / cookies | one profile; switch = full restart | `userDataDir`/`persist`, `sessionContext` in **and** out | profile manager, copy-on-launch | per driver |
| Isolation mechanism | none | full browser restart between sessions | process per instance | process per driver |
| Health / crash recovery | 20 s `page.title()` ping → close, relaunch on next op | Puppeteer `onDisconnect` → relaunch | 500 ms `/health` poll, 45 s startup budget, failure classification, events | none |
| Idle reaping | whole browser after `ULTRASTEALTH_IDLE_TIMEOUT` (1800 s) | `KILL_TIMEOUT` env | per-tab `close_idle` + per-session idle TTL + agent-binding LRU | none |
| Memory ceiling | **none** | none found | none found *(inferred: not in `internal/config`)* | none |
| Queueing / backpressure | none | none | `POST /tasks` scheduler: `MaxQueueSize`, `MaxPerAgent`, `MaxPerAgentFlight`, `ResultTTL`, cancel | none |
| Auth | AF_UNIX socket, `chmod 0600` | **none** in `api/src/env.ts`, binds `0.0.0.0` | bearer token + durable session tokens + rate limit + audit | N/A |
| Container / remote | local only, Xvfb on Linux | Dockerfile w/ chromium + xvfb + nginx, compose, render.yaml | Dockerfile + entrypoint, token via Docker secret | Dockerfile for tests |
| Agent surface | ~45 MCP tools, `eN` refs, `browser_batch` | REST/OpenAPI + WS, no MCP server in-repo | ~44 MCP tools + REST + CLI + dashboard | Python API only |

## steel-browser

**Session model.** `api/src/services/session.service.ts` holds `public activeSession: Session` — *singular* — plus `pastSessions: Session[]`. `handleGetSessions` (`api/src/modules/sessions/sessions.controller.ts:128`) returns `[currentSession, ...pastSessions]`. So the "Open Source Browser API for AI Agents" is, per container, **also single-session**: `startSession()` tears down and relaunches Chrome with the new config; `endSession()` closes it, closes the proxy, records duration/`proxyTxBytes`/`proxyRxBytes`, pushes to history, and mints a fresh idle UUID via `resetSessionInfo()`. Isolation is therefore total (whole-process) but concurrency is zero. Horizontal scale is N containers *(inferred from `docker-compose.yml` + `render.yaml`, which scale the whole api service)*.

The genuinely good part is **session portability**. `CreateSession` (`sessions.schema.ts`) accepts `sessionContext: {cookies, localStorage}`, `proxyUrl`, `userAgent`, `fingerprint`, `timezone`, `dimensions`, `extensions`, `deviceConfig: desktop|mobile`, `optimizeBandwidth: {blockImages, blockMedia, blockStylesheets, blockHosts, blockUrlPatterns}`, `userPreferences`, `caCertificates`. And `GET /v1/sessions/:id/context` extracts state back out — `api/src/services/context/chrome-context.service.ts` reads Chrome's LevelDB directly for localStorage and sessionStorage. Dump → recreate elsewhere is a real primitive. Timezone is auto-derived from the proxy IP (`TimezoneFetcher`) so the two never disagree.

**Concurrency.** Only `createBrowserContext(proxy.url)` in `actions.controller.ts` — a throwaway context for proxied `/scrape` calls. Not a pool.

**API surface.** Fastify + zod + OpenAPI: `POST|GET /sessions`, `GET /sessions/:id`, `GET /sessions/:id/context`, `POST /sessions/:id/release`, `/health`; actions `POST /scrape|/screenshot|/pdf|/search`; CDP passthrough on `:9223` plus a `/v1/devtools/inspector.html` debugger URL returned in every session response. WebSockets: `/v1/sessions/recording` (events from a bundled recorder extension, `api/extensions/recorder/`), cast/screencast, logs, pageId.

**Worth stealing.** `/scrape` is a structured-extraction endpoint, not a DOM dump — `api/src/utils/scrape/` has `readability.ts` (defuddle), `cleanHtml.ts`, `jsonToMarkdown.ts`, `stripBase64Images.ts`, `pdfToHtml.ts` (mupdf). Every session response also carries `debugUrl`/`sessionViewerUrl` so a human can watch the agent work.

**Ops gap:** `api/src/env.ts` has no auth variable at all and `HOST` defaults to `0.0.0.0`. `nginx.conf` is present but does not authenticate.

## pinchtab

**Session model.** Two different things are called "session" here, and both are useful. (1) `internal/session/store.go` — durable, revocable **auth** sessions: token hash → agentId, `IdleTimeout` (default 7 d), `MaxLifetime`, disk persistence, and `LifecycleHook`s firing `revoked|expired|pruned`. (2) Browser state lives in *instances* and *profiles* (`internal/profiles/`), one Chrome per instance.

The glue is `internal/orchestrator/bindings.go`: `sessionID→instanceID`, `agentID→instanceID` (idle TTL + LRU cap, since agents have no lifecycle signal), and `sessionID→tabID→instanceID` ownership. Bindings are written only after a successful proxy and re-validated against running instances on read; session lifecycle hooks evict them. A request touching a tab owned by another instance either rebinds the caller or returns `409 cross_instance_tab` under `strictCrossInstanceTab`. This is sticky routing done properly — worth copying wholesale if ultrastealth ever grows past one browser.

**Concurrency.** `internal/instance/manager.go` is a facade over Repository (lifecycle) / Locator (tab→instance cache) / Allocator. Allocation policies are pluggable and hot-swappable: `internal/instance/allocation/{fcfs,round_robin,random}.go`, `SetAllocationPolicy(name)`. Health is the strongest in the cohort — `internal/orchestrator/health.go` `probeStartupHealth()` polls `/health` every 500 ms against a 45 s `instanceStartupTimeout`, races that against `cmd.Wait()` to distinguish *exited early* from *never became ready*, tails a log ring buffer into `inst.Error`, runs `ClassifyLaunchFailure` for a reason code, and emits `instance.started`/`instance.error` events. An attach path (`POST /instances/attach`) registers an externally-managed Chrome under a policy gate and never starts or stops it.

Backpressure is a separate opt-in layer (`internal/scheduler/`): `POST /tasks`, `/tasks/batch`, `/tasks/{id}/cancel`, `GET /scheduler/stats`, with `MaxQueueSize`, `MaxPerAgent`, `MaxPerAgentFlight`, per-agent fair dequeue, cooperative cancellation, TTL'd results. Tab-level reaping via `TabLifecyclePolicy: "close_idle"` + `TabCloseDelay` (5 m).

**API surface.** ~44 MCP tools in `internal/mcp/tools.go`, and they are more thoughtfully shaped for an LLM than ultrastealth's. Three things stand out:

- **Token budgeting is a parameter.** `pinchtab_snapshot` takes `compact`, `diff`, `depth`, `selector` scoping, and **`maxTokens`**. Its own description steers the model away from itself: *"Use this sparingly: prefer pinchtab_find + action selectors for faster loops."*
- **A semantic finder.** `pinchtab_find` takes natural language and returns `best_ref`. Pipeline (`docs/architecture/find.md`): a11y snapshot → DOM enrichment → descriptors (`ref, role, name, value, label, placeholder, alt, title`) → lexical matcher + embedding matcher → combined score → best ref → intent cache with recovery hooks. Every action tool accepts a unified selector grammar — ref | CSS | XPath | text | `find:` | role | label | placeholder | alt | title | testid | first/last/nth — and `ref` is *deprecated* in favor of it.
- **`pinchtab_capture`.** One call returns a screenshot *and* an a11y snapshot from the same DOM epoch: `{epoch:{frameId,loaderId,domEpoch}, image:{coordinateSpace,devicePixelRatio,viewport,clip}, snapshot:{nodes:[{…, boundingBox:{x,y,w,h}, visible}]}}`. A vision model can overlay refs on pixels with no coordinate guesswork. `withBounds:false` opts out of the per-node `DOM.getBoxModel` cost.

Also: `snap:true` on navigate/click/fill/select (ultrastealth's `--snapshot-after`), `pinchtab_frame` for iframe scoping, one-shot `dialogAction`/`dialogText` on click, and a per-call `browser` param. `docs/architecture/routing-contract.md` formalizes the last one: providers declare `CanHandle(intent)` → `Handle|Skip|Fail` over request *shapes* (`static-read`, `rendered-read`, `visual`, `interaction`, `session-state`, `network-control`, `download-upload`) plus a `StateChanging` flag; `ghost-chrome` serves cheap reads over HTTP and auto-escalates to real Chrome on SPA markers or thin content. Security denials are explicitly non-fallbackable.

**Ops.** `docker-entrypoint.sh` generates config on first boot, binds `0.0.0.0`, reads `PINCHTAB_TOKEN`/`PINCHTAB_TOKEN_FILE` (Docker secrets). `internal/authn/` has cookie, forwarded-header, rate-limit, and audit modules. Headless vs headed is per-instance (`mode` on `POST /instances/start`).

**Worth stealing.** `maxTokens`, `find`, `capture`, the bindings table, and the tool-description-as-steering trick.

## SeleniumBase UC/CDP mode: the disconnect/reconnect evasion

Precise mechanics, from `seleniumbase/undetected/__init__.py` and `seleniumbase/core/browser_launcher.py`:

- **`disconnect()`** (`undetected/__init__.py:521`) — if `self.service.is_connectable()`: `stop_client()`, `service.send_remote_shutdown_command()`, `service._terminate_process()`; sets `_is_connected = False`. **Chrome keeps running.** Only the chromedriver process and the WebDriver session die.
- **`connect()`** (`:538`) — `service.start()` then `start_session()`, which mints a *new* WebDriver session against the still-live browser. It then walks `window_handles`, works around a `chrome-extension://` bug (crbug 396611138) by bouncing the service again on Linux, and switches to the last window.
- **`reconnect(timeout=0.1)`** (`:469`) — disconnect, sleep, connect. `timeout="breakpoint"` drops into `pdb`.
- **`uc_open_with_reconnect(driver, url, reconnect_time=None)`** (`browser_launcher.py:588`) — navigates via injected `window.open(url,"_blank")` + `driver.close()` rather than a chromedriver `Navigate` command, then `driver.reconnect(reconnect_time)`. Default is `constants.UC.RECONNECT_TIME = 2.4` seconds (`seleniumbase/fixtures/constants.py:369`). Passing `"disconnect"` leaves it disconnected indefinitely.
- **`uc_open_with_disconnect(driver, url, timeout=None)`** (`:1006`) — same navigation trick, then disconnect and sleep; the docstring is explicit that no Selenium action works until `driver.connect()`.
- **CDP Mode** (`uc_open_with_cdp_mode`, `:636`) is the endgame: `driver.disconnect()` **first**, read host/port from `_get_cdp_details()`, then start a pure-CDP driver (`undetected/cdp_driver/cdp_util.start`) against the same browser. Chromedriver stays dead for the rest of the run.
- While disconnected, interaction happens at the **OS** layer — `uc_click` and the `uc_gui_*` family (`:1035`, `:1190+`) drive PyAutoGUI, gated by `verify_pyautogui_has_a_headed_browser`.

**What it defeats.** The window during which a challenge script runs contains no live automation client: no chromedriver HTTP service, no open WebDriver session, and no CDP client attached. Page-side probes that look for an attached debugger or for the observable side effects of an active session therefore see nothing. Navigation via `window.open` also avoids the chromedriver-issued navigation command signature. *(Inferred: the source does not enumerate which specific vendor checks this defeats; `help_docs/uc_mode.md:148` only states that UC Mode substitutes `uc_open_with_reconnect` for `driver.get` when a preceding `requests.get` detects anti-bot services.)*

**Cost.** (a) A hard ~2.4 s per protected navigation. (b) Zero driver commands during the window — you must decide everything beforehand. (c) `start_session()` on reconnect discards *driver-side* state; window handles and frame scope must be re-established (the code does exactly this, defensively). (d) Interaction while disconnected needs a real display: `help_docs/uc_mode.md:144` states UC Mode is detectable in headless mode and prescribes `xvfb=True` on Linux instead.

**Tension with ultrastealth's warm daemon — this is a direct architectural conflict.** `daemon.py`'s own docstring states the premise: *"Exactly one process holds the CDP connection ... which is what causes reconnect churn / Network.enable timeouts."* Ultrastealth's speed comes from never dropping that connection. An equivalent evasion would need:

1. A `detached` mode that suspends `_health_watchdog` (`daemon.py:125`) — it pings `page.title()` every 20 s and, on failure, calls `browser_core.close()`. A deliberate disconnect would be read as a wedged browser and **kill the session**. `_idle_reaper` needs the same suspension.
2. Re-resolution of `browser_core._page` after re-attach, and invalidation of `_ref_maps` / `_prev_ref_signatures` (both keyed by `_page_id(page)`, which reads `page._guid` — a new connection means new guids, so every `eN` ref goes stale).
3. A CDP connection ultrastealth controls directly. `fetcher.py`'s persistent context via rebrowser-playwright cannot be detached and re-attached while keeping `Page` handles. Realistically this means launching with `--remote-debugging-port` and using `connect_over_cdp` for a "shielded navigation" mode.

Verdict: technically feasible, but it converts the warm daemon into a warm *browser* with a cold *connection* for the duration, and every ref the agent holds is invalidated across the window. Worth prototyping as an explicit opt-in op (`shielded_navigate`), not as the default path.

## Agent-ergonomics ideas worth adopting

1. **`max_tokens` on `snapshot`** — ultrastealth's `interactive`/`compact`/`diff` triad has no hard ceiling, so one heavy SPA can blow the context window in a single call.
2. **A semantic `find`** — natural language → `best_ref`, letting the agent skip the snapshot on the common path. `_walk_tree` (`browser_core.py:83`) already builds the descriptor material; a lexical matcher over `role`/`name` is small, embeddings optional.
3. **Unified selector grammar** — `_resolve` (`:135`) does ref-or-CSS only. Adding `text:`, `role:`, `testid:`, `nth:` prefixes removes a class of failed round-trips.
4. **Paired capture** — screenshot + snapshot from one DOM epoch with per-node `boundingBox`/`visible`. Today an agent doing vision work calls `browser_screenshot` and `browser_snapshot` separately and hopes the DOM didn't move.
5. **Structured extraction op** — `browser_get(kind="html")` returns raw HTML; a `kind="markdown"` (readability, per steel's `api/src/utils/scrape/`) cuts extraction responses by an order of magnitude. Obscura's `LP.getMarkdown` CDP method is cheaper still where the runner supports it.
6. **Keep the typed errors.** `BrowserCoreError(type, message)` with `stale_ref`/`navigation_failed`/`bad_op`, and `batch`'s per-step `{ok, op, error}` with `stop_on_error`, are *ahead* of both peers (steel returns `{success:false, message}` strings). Don't regress; do add `retryable: bool` so callers know whether re-snapshotting fixes it.
7. **Tool descriptions as steering** — pinchtab tells the model which tool *not* to reach for. Cheap, effective.
8. **Session context export/import already exists** — `browser_state_save`/`browser_state_load` (`mcp_server.py:1228`, `:1259`) cover steel's `/sessions/:id/context`. Only per-session scoping is missing.

## Path to multi-session / pooled ultrastealth

The blocker is that `browser_core.py` keeps everything in module globals — `_fetcher`, `_page`, `_browser_config`, `_ref_maps`, `_prev_ref_signatures`, `_op_lock` (`:27-33`) — and all ~30 ops call `await get_page()` against them.

1. **`browser_core.py`** — a `Session` dataclass (`fetcher`, `page`, `config`, `ref_maps`, `prev_ref_signatures`, `op_lock`, `last_used`) and a `SESSIONS: dict[str, Session]` registry. Cheapest migration that avoids touching 30 signatures: hold the current session in a `contextvars.ContextVar[Session]` and rewrite only `get_page()`, `_resolve()`, `_page_id()`. Key ref-maps by `(session_id, page_guid)` — `_page_id` takes the last 6 chars of `page._guid`, fine for one process and collision-prone across N *(inferred)*.
2. **`daemon.py`** — `dispatch()` reads `request["session"]` (default `"default"`), resolves or creates via a `SessionManager`, and acquires **that session's** lock rather than the module-wide `browser_core._op_lock` (`:50`). That one change unlocks real concurrency. `_health_watchdog` and `_idle_reaper` iterate sessions, reaping on `last_used`. Add `ULTRASTEALTH_MAX_SESSIONS` and an RSS ceiling polled off the Chrome pid with LRU eviction — neither exists today, and Chrome is 300-600 MB apiece.
3. **`client.py`** — `connect(session="…")`, thread the id into `call()`. Per-request short connections stay as-is.
4. **`mcp_server.py`** — one `browser_session` tool (create/list/release/switch) plus a server-level current-session, so the existing ~45 tools keep working unchanged. An optional `session` param on every tool is the alternative but multiplies the schema surface.
5. **Risks.** Two sessions cannot share one Chrome `user_data_dir` — either copy profiles per session (pinchtab's `internal/profiles/copydir.go` model) or isolate at the Playwright-context level. Context-level is far cheaper but wrong here: ultrastealth's stealth lives in `fetcher.py`'s *launch* path, so shared-process contexts would share one fingerprint, one proxy, and one crash domain. Recommend process-per-session. Also, `ensure_browser()` (`:352`) closes the browser when the requested profile differs; that becomes "find or create the session matching this config", and the default one-shot path must not regress.

## Recommended actions

| # | Action | Rationale | Effort |
|---|---|---|---|
| 1 | Per-session `op_lock` instead of the module-global one in `daemon.py:50` | The single change that turns serialized ops into concurrency; harmless with one session | **S** |
| 2 | `max_tokens` on `browser_snapshot` | Prevents one heavy page from consuming an agent's whole context; pinchtab-proven | **S** |
| 3 | Add `retryable: bool` to `BrowserCoreError` payloads | Callers currently guess whether `stale_ref` means re-snapshot; make it explicit | **S** |
| 4 | Extend `_resolve` with a prefixed selector grammar (`text:`, `role:`, `testid:`, `nth:`) | Removes failed-lookup round-trips; ~20 lines in `browser_core.py:135` | **S** |
| 5 | `Session` dataclass + registry behind a `contextvars` shim | Unblocks everything else without rewriting 30 op signatures | **M** |
| 6 | `browser_session` MCP tool (create/list/release/switch) | Session control without multiplying the schema of all ~45 tools | **M** |
| 7 | Semantic `find` returning `best_ref` | Biggest single token win; descriptor data already exists in `_walk_tree` | **M** |
| 8 | Paired `capture` (screenshot + snapshot, same epoch, with bounding boxes) | Makes vision-model use correct rather than hopeful | **M** |
| 9 | Markdown/readability extraction op | Order-of-magnitude smaller extraction responses than `get(kind="html")` | **M** |
| 10 | Per-session RSS ceiling + LRU eviction + per-session idle reaping | Without it, a pool OOMs the host; no peer here solves it either — genuine differentiator | **M** |
| 11 | Sticky agent→session bindings with idle TTL, modeled on `internal/orchestrator/bindings.go` | Only needed once >1 session exists, but design it in early | **M** |
| 12 | Containerized mode: Dockerfile (chromium + Xvfb + dbus), TCP/HTTP transport, token auth, `0.0.0.0` bind | Today's AF_UNIX + `chmod 0600` is the *entire* security model; remote mode needs a real one. Note steel-browser ships **no auth at all** — do not copy that | **L** |
| 13 | Opt-in `shielded_navigate` (UC-style disconnect/reconnect) | Real stealth gain, but conflicts with the warm daemon — needs watchdog suspension, ref invalidation, and a `connect_over_cdp` launch path. Prototype behind a flag; do not make it default | **L** |
