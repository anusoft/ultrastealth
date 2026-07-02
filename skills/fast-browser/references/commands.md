# Ultrastealth CLI — command reference

All commands attach to the warm daemon. Global flags go **before** the group:
`ultrastealth [--json] [--socket PATH] [--no-autostart] <group> ...`

- `--json` — raw JSON output (default is pretty JSON / plain text).
- `--socket PATH` — target a specific daemon socket (default
  `~/.ultrastealth/daemon.sock`, overridable via `ULTRASTEALTH_DAEMON_DIR`).
- `--no-autostart` — error instead of auto-starting a daemon.

`ultrastealth` and `us` are the same binary.

## Daemon lifecycle

| Command | What it does |
|---|---|
| `ultrastealth daemon start` | Start the warm browser daemon in the background |
| `ultrastealth daemon stop` | Stop the daemon and close the browser |
| `ultrastealth daemon status` | Show `running`, `socket`, `pid` |
| `ultrastealth daemon logs` | Print the daemon log |
| `ultrastealth daemon run` | Run the daemon in the foreground (used internally by `start`) |

Env: `ULTRASTEALTH_IDLE_TIMEOUT` (seconds; `0` = never close the browser, default
`1800`), `ULTRASTEALTH_DAEMON_DIR`, plus the usual `ULTRASTEALTH_RUNNER` /
`ULTRASTEALTH_USER_DATA_DIR` / `ULTRASTEALTH_PROFILE_DIRECTORY` for profile choice.

## Navigation

| Command | Example |
|---|---|
| `browser navigate <url> [--wait-secs N] [--snapshot-after]` | `us browser navigate https://example.com` |
| `browser back [--snapshot-after]` | `us browser back` |
| `browser reload [--snapshot-after]` | `us browser reload` |
| `browser url` | `us browser url` |
| `browser title` | `us browser title` |

## Inspect

| Command | Example |
|---|---|
| `browser snapshot [--interactive] [--compact] [--diff]` | `us browser snapshot --interactive --compact` |
| `browser get <text\|html\|attr\|url\|title> [target] [--attribute A]` | `us browser get text "#status"` |
| `browser is <visible\|enabled\|checked> <target>` | `us browser is visible "#submit"` |
| `browser screenshot [--out FILE] [--full-page]` | `us browser screenshot --out shot.png` |

`target` is a snapshot ref (`e2`) or a CSS selector (`#id`, `.class`).

## Interact (all accept `--snapshot-after`)

| Command | Example |
|---|---|
| `browser click <target>` | `us browser click e2` |
| `browser hover <target>` | `us browser hover e2` |
| `browser focus <target>` | `us browser focus "#email"` |
| `browser scroll-into-view <target>` | `us browser scroll-into-view e9` |
| `browser type <target> --text T [--submit]` | `us browser type e5 --text "hi" --submit` |
| `browser fill <target> --text T` | `us browser fill "#email" --text "a@b.com"` |
| `browser select <target> --value V` | `us browser select "#country" --value US` |
| `browser press <key>` | `us browser press Enter` |
| `browser scroll [--direction up\|down] [--amount N]` | `us browser scroll --amount 800` |

## Wait

| Command | Example |
|---|---|
| `browser wait --selector S [--timeout-ms N]` | `us browser wait --selector "#ready"` |
| `browser wait --text T` | `us browser wait --text "Welcome"` |
| `browser wait --url-contains S` | `us browser wait --url-contains "/dashboard"` |
| `browser wait --load-state S` | `us browser wait --load-state networkidle` |
| `browser wait --function JS` | `us browser wait --function "window.ready===true"` |

## Scripting

| Command | Example |
|---|---|
| `browser eval <js>` | `us browser eval "() => document.title"` |
| `browser batch <file.json\|->` | `us browser batch steps.json` |

`batch` runs an array of `{"op": name, ...args}` in one call, stopping on the
first error; end with `{"op":"snapshot"}` to get fresh refs back. Op names match
the subcommands (`navigate`, `wait`, `click`, `type`, `fill`, `press`, `hover`,
`focus`, `scroll_into_view`, `select`, `scroll`, `get`, `is`, `evaluate`,
`snapshot`, `screenshot`, `go_back`, `reload`).
