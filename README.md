# cpa-warden

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Release](https://img.shields.io/github/v/release/fantasticjoe/cpa-warden)](https://github.com/fantasticjoe/cpa-warden/releases/latest)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)
![uv](https://img.shields.io/badge/deps-uv-6f42c1)

[简体中文](README.zh-CN.md)

`cpa-warden` is an interactive [CLIProxyAPI (CPA)](https://github.com/router-for-me/CLIProxyAPI) auth inventory scanner and maintenance tool for local operations against a specific CPA management environment.

It currently relies on two management flows: `GET /v0/management/auth-files` for inventory and `POST /v0/management/api-call` against `https://chatgpt.com/backend-api/wham/usage` for usage probing.

## What It Does

From a user perspective, the script:

- fetches the current auth inventory
- stores local state in SQLite
- probes usage concurrently
- exports current `401` and quota-limited results
- optionally deletes, disables, or re-enables accounts in maintenance mode

## Key Capabilities

- Interactive mode by default when no `--mode` is provided in a TTY
- Non-interactive `scan` and `maintain` workflows for repeatable runs
- External JSON configuration for sensitive values such as `base_url` and `token`
- Concurrent usage probing through the CLIProxyAPI `api-call` endpoint
- Local SQLite state tracking across runs
- JSON exports for invalid and quota-limited accounts
- Short production output with optional Rich progress display in TTY sessions
- Full debug-level logs written to a file on every run

## Safety And Scope

`cpa-warden` is built for a specific CPA management setup, not as a generic account-management platform. It is already usable for real local operations, but it is still an early-stage open-source project. Treat it as an operator-focused maintenance tool for known CPA environments, not a universal solution for arbitrary auth systems.

Maintenance mode can delete or disable remote accounts. Review your configuration carefully before running destructive actions, especially when using `--quota-action delete` or `--yes`.

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)

## Installation

```bash
uv sync
```

## Configuration

Copy the example configuration first:

```bash
cp config.example.json config.json
```

At minimum, set:

- `base_url`
- `token`

`config.json` is ignored by git and should never be committed.

Example configuration:

```json
{
  "base_url": "https://your-cpa.example.com",
  "token": "replace-with-your-management-token",
  "target_type": "codex",
  "provider": "",
  "probe_workers": 40,
  "action_workers": 20,
  "timeout": 15,
  "retries": 1,
  "quota_action": "disable",
  "delete_401": true,
  "auto_reenable": true,
  "db_path": "cpa_warden_state.sqlite3",
  "invalid_output": "cpa_warden_401_accounts.json",
  "quota_output": "cpa_warden_quota_accounts.json",
  "log_file": "cpa_warden.log",
  "debug": false,
  "user_agent": "codex_cli_rs/0.76.0 (Debian 13.0.0; x86_64) WindowsTerminal"
}
```

Important configuration keys:

- `base_url`: CLIProxyAPI management base URL
- `token`: CLIProxyAPI management token
- `target_type`: filter records by `files[].type`
- `provider`: filter records by the `provider` field
- `probe_workers`: concurrency for usage probing
- `action_workers`: concurrency for delete / disable / enable actions
- `timeout`: request timeout in seconds
- `retries`: retry count for probe failures
- `quota_action`: action for quota-limited accounts, either `disable` or `delete`
- `delete_401`: whether maintenance mode deletes `401` accounts
- `auto_reenable`: whether recovered accounts are re-enabled automatically
- `db_path`: local SQLite state database path
- `invalid_output`: JSON export path for invalid `401` accounts
- `quota_output`: JSON export path for quota-limited accounts
- `log_file`: runtime log file path
- `debug`: enable more verbose terminal logging
- `user_agent`: User-Agent used for `wham/usage` probing

## Usage

Interactive mode:

```bash
uv run python cpa_warden.py
```

Non-interactive examples:

```bash
uv run python cpa_warden.py --mode scan
uv run python cpa_warden.py --mode scan --debug
uv run python cpa_warden.py --mode scan --target-type codex --provider openai
uv run python cpa_warden.py --mode maintain
uv run python cpa_warden.py --mode maintain --no-delete-401 --no-auto-reenable
uv run python cpa_warden.py --mode maintain --quota-action delete
uv run python cpa_warden.py --mode maintain --quota-action delete --yes
```

Available CLI options:

- `--config`
- `--mode`
- `--target-type`
- `--provider`
- `--probe-workers`
- `--action-workers`
- `--timeout`
- `--retries`
- `--user-agent`
- `--quota-action`
- `--db-path`
- `--invalid-output`
- `--quota-output`
- `--log-file`
- `--debug`
- `--delete-401`
- `--no-delete-401`
- `--auto-reenable`
- `--no-auto-reenable`
- `--yes`

## Modes

### `scan`

`scan` only reads inventory, probes usage, updates the local SQLite state, and exports current results. It does not change remote account state.

### `maintain`

`maintain` always runs a full `scan` first, then applies configured actions:

- delete `401` accounts if enabled
- disable or delete quota-limited accounts
- re-enable recovered accounts if enabled

Deletion flows require confirmation unless `--yes` is provided.

## Filters And Classification Rules

Filtering behavior:

- `target_type` matches `files[].type`
- `provider` is a case-insensitive exact match against the `provider` field

Classification rules:

- `401`: `unavailable == true` or `api-call.status_code == 401`
- `quota limited`: `api-call.status_code == 200` and `body.rate_limit.limit_reached == true`
- `recovered`: previously marked as `quota_disabled`, and now `allowed == true` and `limit_reached == false`

## Output Files

Default output artifacts:

- `cpa_warden_state.sqlite3`: local SQLite state database
- `cpa_warden_401_accounts.json`: exported invalid `401` accounts
- `cpa_warden_quota_accounts.json`: exported quota-limited accounts
- `cpa_warden.log`: runtime log file

## Logging And Debug Behavior

- Production terminal output stays short
- If the session is a TTY, production mode prefers a Rich progress display
- `--debug` or `debug: true` enables more verbose terminal logging
- The log file always keeps full debug-level details

## Project Structure

- `cpa_warden.py`: main entrypoint
- `clean_codex_accounts.py`: compatibility wrapper for the old command name
- `config.example.json`: example configuration
- `pyproject.toml`: project metadata and dependencies
- `.github/workflows/ci.yml`: basic CI checks

## Compatibility Note

`clean_codex_accounts.py` is kept as a compatibility wrapper around `cpa_warden.main()`. Use `cpa_warden.py` as the primary documented entrypoint for new usage.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## Changelog

See [CHANGELOG.md](CHANGELOG.md).

## Security

See [SECURITY.md](SECURITY.md) for responsible disclosure guidance.

## License

MIT. See [LICENSE](LICENSE).
