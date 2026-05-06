# cpa-warden

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Release](https://img.shields.io/github/v/release/fantasticjoe/cpa-warden)](https://github.com/fantasticjoe/cpa-warden/releases/latest)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)
![uv](https://img.shields.io/badge/deps-uv-6f42c1)

[简体中文](README.zh-CN.md)

`cpa-warden` is an interactive [CLIProxyAPI (CPA)](https://github.com/router-for-me/CLIProxyAPI) auth inventory scanner, uploader, and maintenance tool for local operations against a specific CPA management environment.

It currently relies on three management flows: `GET /v0/management/auth-files` for inventory, `POST /v0/management/api-call` against `https://chatgpt.com/backend-api/wham/usage` for usage probing, and `POST /v0/management/auth-files` for auth-file uploads.

## What It Does

From a user perspective, the script:

- fetches the current auth inventory
- stores local state in SQLite
- probes usage concurrently
- uploads local auth JSON files concurrently in upload mode
- exports current `401` and quota-limited results
- optionally deletes, disables, or re-enables accounts in maintenance mode
- can refill accounts to a minimum valid threshold after maintenance

## Key Capabilities

- Interactive mode by default when no `--mode` is provided in a TTY
- Non-interactive `scan`, `maintain`, `upload`, and `maintain-refill` workflows for repeatable runs
- External JSON configuration for sensitive values such as `base_url` and `token`
- Concurrent usage probing through the CLIProxyAPI `api-call` endpoint
- Concurrent auth-file uploads with local SQLite deduplication guards
- Optional external register-command hook when refill still cannot meet threshold
- Local SQLite state tracking across runs
- JSON exports for invalid and quota-limited accounts
- Short production output with optional Rich progress display in TTY sessions
- Full debug-level logs written to a file on every run

## Safety And Scope

`cpa-warden` is built for a specific CPA management setup, not as a generic account-management platform. It is already usable for real local operations, but it is still an early-stage open-source project. Treat it as an operator-focused maintenance tool for known CPA environments, not a universal solution for arbitrary auth systems.

Maintenance mode can delete or disable remote accounts. Upload mode can create new remote auth entries and optionally force overwrite attempts by name. `maintain-refill` can execute an external register command when enabled. Review your configuration carefully before running destructive actions, especially when using `--quota-action delete`, `--upload-force`, `--auto-register`, or `--yes`.

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
  "probe_workers": 100,
  "action_workers": 100,
  "timeout": 15,
  "retries": 3,
  "delete_retries": 2,
  "quota_action": "disable",
  "quota_disable_threshold": 0.0,
  "delete_401": true,
  "auto_reenable": true,
  "reenable_scope": "signal",
  "upload_dir": "",
  "upload_workers": 20,
  "upload_retries": 2,
  "upload_method": "json",
  "upload_recursive": false,
  "upload_force": false,
  "min_valid_accounts": 100,
  "refill_strategy": "to-threshold",
  "auto_register": false,
  "register_command": "",
  "register_timeout": 300,
  "register_workdir": "",
  "db_path": "cpa_warden_state.sqlite3",
  "invalid_output": "cpa_warden_401_accounts.json",
  "quota_output": "cpa_warden_quota_accounts.json",
  "log_file": "cpa_warden.log",
  "debug": false,
  "user_agent": "codex_cli_rs/0.76.0 (Debian 13.0.0; x86_64) WindowsTerminal"
}
```

Boolean-like config values are parsed strictly. Valid forms are: `true/false`, `1/0`, `yes/no`, `on/off` (case-insensitive). Invalid values fail fast at startup.

Important configuration keys:

- `base_url`: CLIProxyAPI management base URL
- `token`: CLIProxyAPI management token
- `target_type`: filter records by `files[].type`
- `provider`: filter records by the `provider` field
- `probe_workers`: concurrency for usage probing
- `action_workers`: concurrency for delete / disable / enable actions
- `timeout`: request timeout in seconds
- `retries`: retry count for retryable probe failures (timeouts, network errors, `429`, `5xx`)
- `delete_retries`: retry count for failed delete actions (network errors, `429`, `5xx`, etc.)
- `quota_action`: action for quota-limited accounts, either `disable` or `delete`
- `quota_disable_threshold`: auto-disable threshold by remaining quota ratio (`0~1`); `0` keeps legacy behavior (disable only when exhausted)
- `delete_401`: whether maintenance mode deletes `401` accounts
- `auto_reenable`: whether recovered accounts are re-enabled automatically
- `reenable_scope`: scope for auto re-enable, `signal` or `managed` (default: `signal`)
- `upload_dir`: source directory for `upload` mode (`.json` files only)
- `upload_workers`: concurrent workers for `upload` mode
- `upload_retries`: retry count for upload failures
- `upload_method`: upload request type, `json` or `multipart`
- `upload_recursive`: whether `upload` mode scans subdirectories recursively
- `upload_force`: whether to upload even when remote file name already exists
- `min_valid_accounts`: minimum valid-account threshold in `maintain-refill` mode
- `refill_strategy`: refill rule in `maintain-refill`, `to-threshold` or `fixed`
- `auto_register`: whether to invoke an external register command when still below threshold
- `register_command`: external register command template (logic stays outside this repo)
- `register_timeout`: timeout (seconds) for external register command
- `register_workdir`: optional working directory for external register command
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

`scan` scenarios:

- Baseline inventory and usage probe without changing remote state:  
  `uv run python cpa_warden.py --mode scan`
- Troubleshoot probing behavior with verbose terminal logs:  
  `uv run python cpa_warden.py --mode scan --debug`
- Focus on a specific account segment (type + provider):  
  `uv run python cpa_warden.py --mode scan --target-type codex --provider openai`

`maintain` scenarios:

- Standard maintenance pass using configured defaults:  
  `uv run python cpa_warden.py --mode maintain`
- Review maintenance results without deleting `401` or auto re-enable:  
  `uv run python cpa_warden.py --mode maintain --no-delete-401 --no-auto-reenable`
- Treat quota-limited accounts aggressively by deleting them:  
  `uv run python cpa_warden.py --mode maintain --quota-action delete`
- Disable accounts early when remaining quota ratio drops to 10%:  
  `uv run python cpa_warden.py --mode maintain --quota-disable-threshold 0.1`
- Re-enable only accounts previously managed by this tool:  
  `uv run python cpa_warden.py --mode maintain --reenable-scope managed`
- Run destructive maintenance non-interactively (skip confirmation):  
  `uv run python cpa_warden.py --mode maintain --quota-action delete --yes`

`upload` scenarios:

- Upload auth files from a single directory:  
  `uv run python cpa_warden.py --mode upload --upload-dir ./auth_files`
- Bulk upload across nested folders with higher concurrency:  
  `uv run python cpa_warden.py --mode upload --upload-dir ./auth_files --upload-recursive --upload-workers 50`
- Use multipart upload and force same-name overwrite attempts:  
  `uv run python cpa_warden.py --mode upload --upload-dir ./auth_files --upload-method multipart --upload-force`

`maintain-refill` scenarios:

- Maintain first, then refill only the gap to target capacity:  
  `uv run python cpa_warden.py --mode maintain-refill --min-valid-accounts 200 --upload-dir ./auth_files`
- Maintain first, then refill with a fixed-size upload batch:  
  `uv run python cpa_warden.py --mode maintain-refill --min-valid-accounts 200 --refill-strategy fixed --upload-dir ./auth_files`
- Enable external register fallback when refill still misses threshold:  
  `uv run python cpa_warden.py --mode maintain-refill --min-valid-accounts 200 --upload-dir ./auth_files --auto-register --register-command 'python /opt/register-machine/register.py'`

Available CLI options:

- `--config`
- `--mode`
- `--target-type`
- `--provider`
- `--probe-workers`
- `--action-workers`
- `--timeout`
- `--retries`
- `--delete-retries`
- `--user-agent`
- `--quota-action`
- `--quota-disable-threshold`
- `--db-path`
- `--invalid-output`
- `--quota-output`
- `--log-file`
- `--debug`
- `--upload-dir`
- `--upload-workers`
- `--upload-retries`
- `--upload-method`
- `--upload-force`
- `--no-upload-force`
- `--min-valid-accounts`
- `--refill-strategy`
- `--auto-register`
- `--no-auto-register`
- `--register-command`
- `--register-timeout`
- `--register-workdir`
- `--delete-401`
- `--no-delete-401`
- `--auto-reenable`
- `--no-auto-reenable`
- `--reenable-scope`
- `--upload-recursive`
- `--no-upload-recursive`
- `--yes`

## Modes

### `scan`

`scan` only reads inventory, probes usage, updates the local SQLite state, and exports current results. It does not change remote account state.

### `maintain`

`maintain` always runs a full `scan` first, then applies configured actions:

- delete `401` accounts if enabled
- disable or delete quota-limited accounts
- re-enable recovered accounts if enabled

Note: default `reenable_scope=signal` means `auto_reenable` can also re-enable manually disabled accounts if they currently satisfy the recovered condition. Use `reenable_scope=managed` to limit re-enable to accounts this tool previously marked as `quota_disabled`.

Deletion flows require confirmation unless `--yes` is provided.

### `upload`

`upload` reads `.json` files from `upload_dir`, validates content, and uploads them concurrently to `/v0/management/auth-files`.

It keeps upload state in SQLite and ensures each `{base_url, file_name, content_sha256}` tuple is uploaded only once, preventing duplicate uploads under concurrent runs.

If any file fails validation or upload, the command exits with a non-zero status.

### `maintain-refill`

`maintain-refill` runs `maintain` first, then checks valid accounts with strict rules (`disabled=0`, non-401, non-quota-limited, and no probe error).  
If valid accounts are below `min_valid_accounts`, it uploads additional auth files from `upload_dir` based on `refill_strategy`:

- `to-threshold`: upload only the gap (`min_valid_accounts - current_valid`)
- `fixed`: upload a fixed count equal to `min_valid_accounts` whenever triggered

If still below threshold and `auto_register=true`, it invokes `register_command` (external logic) with:

- CLI arguments appended by this tool: `--count <N> --output-dir <upload_dir>`
- environment variables: `CPA_REGISTER_COUNT`, `CPA_REGISTER_OUTPUT_DIR`

External registration logic is intentionally out-of-repo; this project only provides the hook.

## Filters And Classification Rules

Filtering behavior:

- `target_type` matches `files[].type`
- `provider` is a case-insensitive exact match against the `provider` field

Classification rules:

- `401`: `unavailable == true` or `api-call.status_code == 401`
- `quota limited`: `api-call.status_code == 200` and either (1) the effective quota signal is exhausted (`limit_reached == true`) or (2) remaining quota ratio is `<= quota_disable_threshold`; for `plan_type=pro` it prefers Spark from `additional_rate_limits` (`metered_feature=codex_bengalfox`, fallback: `limit_name` contains `Spark`) when Spark has a usable `limit_reached` signal, otherwise it falls back to top-level `rate_limit`
- `recovered`: account is currently disabled, and now the same effective signal reports `allowed == true` and `limit_reached == false`

Probe retry rules:

- `429` and `5xx`: retry with backoff up to `retries`
- other `4xx`: fail fast without retry

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
- `config.example.json`: example configuration
- `pyproject.toml`: project metadata and dependencies
- `.github/workflows/ci.yml`: basic CI checks

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## Changelog

See [CHANGELOG.md](CHANGELOG.md).

## Security

See [SECURITY.md](SECURITY.md) for responsible disclosure guidance.

## License

MIT. See [LICENSE](LICENSE).

## Star History

<a href="https://www.star-history.com/?repos=fantasticjoe%2Fcpa-warden&type=date&legend=bottom-right">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=fantasticjoe/cpa-warden&type=date&theme=dark&legend=top-left" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=fantasticjoe/cpa-warden&type=date&legend=top-left" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=fantasticjoe/cpa-warden&type=date&legend=top-left" />
 </picture>
</a>
