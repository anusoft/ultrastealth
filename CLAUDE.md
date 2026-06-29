# Ultrastealth MCP Handoff

## Current Work

- MCP profile selection is implemented on `browser_navigate` and `browser_restart`.
- Both tools accept `profile_directory`, `user_data_dir`, and `runner`.
- When a requested profile differs from the active browser profile, MCP restarts the browser with the requested profile.
- Env or tool profile requests disable temporary-profile fallback so the tool does not silently use the wrong Chrome/Chromium profile.
- Default-profile launch still retries once with a temporary profile when no explicit profile is requested, preserving one-shot navigation when normal Chrome locks the default profile.

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
