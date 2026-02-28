# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project uses [Semantic Versioning](https://semver.org/).

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
