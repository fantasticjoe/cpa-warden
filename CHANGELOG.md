# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.2.0] - 2026-03-09

### Added

- Added `upload` mode to concurrently upload local auth JSON files via `POST /v0/management/auth-files`
- Added upload-related CLI/config options: `upload_dir`, `upload_workers`, `upload_retries`, `upload_method`, `upload_recursive`, `upload_force`
- Added SQLite table `auth_file_uploads` for upload status tracking and idempotent deduplication across concurrent runs
- Added `maintain-refill` mode to enforce a minimum valid-account threshold after maintenance
- Added refill and external register hook options: `min_valid_accounts`, `refill_strategy`, `auto_register`, `register_command`, `register_timeout`, `register_workdir`
- Added quota auto-disable threshold option: `quota_disable_threshold` / `--quota-disable-threshold` (`0~1`, default `0`)
- Added re-enable scope option: `reenable_scope` / `--reenable-scope` (`signal` or `managed`, default `signal`)

### Changed

- Raised default `probe_workers` from 40 to 100
- Raised default `action_workers` from 20 to 100
- Raised default `retries` from 1 to 3
- `upload` mode now exits non-zero when any file validation or upload fails
- `maintain-refill` now exits non-zero when post-maintenance valid accounts remain below threshold
- Updated docs to standardize on `cpa_warden.py` as the documented CLI entrypoint
- Quota classification now supports threshold-based disabling when remaining ratio is `<= quota_disable_threshold` (with `limit_reached` behavior unchanged as fallback)
- Pro-plan quota signal selection now falls back to primary `rate_limit` when Spark signal is incomplete (`limit_reached` unavailable)
- Probe retry behavior now retries `429` and `5xx` responses with backoff; other `4xx` fail fast
- Boolean config parsing is now strict (`true/false/1/0/yes/no/on/off`) to avoid accidental truthy-string misconfiguration
- Recovered-account classification now relies on live usage signals plus current disabled state, with `reenable_scope` to control whether auto-reenable targets all signal-recovered accounts or only tool-managed ones

## [0.1.0] - 2026-03-01

### Added

- Interactive `scan` and `maintain` workflows for local [CLIProxyAPI (CPA)](https://github.com/router-for-me/CLIProxyAPI) account operations
- External JSON configuration for CLIProxyAPI connection settings and runtime behavior
- Concurrent `wham/usage` probing through the CLIProxyAPI `api-call` endpoint
- SQLite state tracking for auth inventory and probe results
- JSON exports for invalid `401` accounts and quota-limited accounts
- Rich progress support for production runs in TTY environments
- Debug logging with full details written to a log file
- English and Simplified Chinese README files
- A contributor guide for open-source changes
- GitHub issue templates and a pull request template
- A CI workflow for dependency sync, bytecode compilation, and CLI help checks

### Changed

- Renamed the project identity from `cpa-clean` to `cpa-warden`
- Clarified account classification around `auth-files` inventory and `wham/usage` probing
- Kept production terminal output concise while preserving detailed logs in the log file
