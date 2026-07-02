# Ultrastealth MCP Handoff

## Current Work

- MCP profile selection is implemented on `browser_navigate` and `browser_restart`.
- Both tools accept `profile_directory`, `user_data_dir`, and `runner`.
- When a requested profile differs from the active browser profile, MCP restarts the browser with the requested profile.
- Env or tool profile requests disable temporary-profile fallback so the tool does not silently use the wrong Chrome/Chromium profile.
- Default-profile launch still retries once with a temporary profile when no explicit profile is requested, preserving one-shot navigation when normal Chrome locks the default profile.

## Warm Daemon + Fast CLI

- A standalone daemon (`ultrastealth daemon start`) owns **one** warm, persistent-profile Chrome; the `ultrastealth`/`us` CLI, the MCP server, and `connect()` scripts all attach to it over a Unix socket. Only the daemon holds the CDP connection (no multi-connection churn).
- Shared engine `browser_core.py` is the single implementation of every op (navigate/snapshot/click/type/wait/get/is/evaluate/batch/…). `daemon.py` serves it as JSON-RPC; `client.py` is the client + `connect()`; `cli.py` is the CLI; the MCP server gained `browser_batch` + `browser_snapshot` and **auto-routes to the daemon when its socket exists** (opt out with `ULTRASTEALTH_MCP_NO_DAEMON=1`).
- Speed levers: warm reuse (no cold start), stable `eN` snapshot refs + `--snapshot-after`, and `browser_batch` (N steps → 1 call). The stealth/bypass launch path in `fetcher.py` is unchanged, so bot-detection parity is preserved.
- Socket/pid/log live under `ULTRASTEALTH_DAEMON_DIR` (default `~/.ultrastealth`); the socket auto-relocates to a short temp path if that dir would exceed the AF_UNIX length limit. `ULTRASTEALTH_IDLE_TIMEOUT` controls keep-warm (`0` = never close).
- Agent playbook + full command list: the bundled `fast-browser` skill (`skills/fast-browser/`).

## Restart Test

For exact-profile tests, prefer Google Chrome for best userAgentData brand parity. Chromium remains available by setting `--runner chromium+default-profile`.

Before restarting MCP for an exact-profile test, close regular Chrome so the shared Chrome user-data dir is not locked. Then start Codex/MCP with:

```toml
[mcp_servers.ultrastealth]
command = "ultrastealth-mcp"
args = ["--transport", "stdio", "--user-data-dir", "/Users/mac/Library/Application Support/Google/Chrome", "--profile-directory", "Profile 1"]
```

After restarting the MCP server, test one-shot navigation:

```json
{"url":"https://www.google.com"}
```

Test a specific Chrome profile:

```json
{"url":"https://mail.google.com","profile_directory":"Profile 1"}
```

If Chrome/Chromium has locked the requested profile, close that browser or pass a separate `user_data_dir`:

```json
{"url":"https://mail.google.com","user_data_dir":"/path/to/Chrome/User Data","profile_directory":"Profile 1"}
```

## Verification

```bash
/Users/mac/.local/pipx/venvs/ultrastealth/bin/python -m unittest discover -s tests
```
