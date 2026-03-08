# Contributing

## Scope Of Contributions

Contributions are welcome for documentation, bug fixes, stability improvements, and CLI usability improvements. Keep changes focused on the current purpose of the project: safe and understandable [CLIProxyAPI (CPA)](https://github.com/router-for-me/CLIProxyAPI) account scanning, upload, maintenance, and refill orchestration for known environments.

## Local Setup

Install dependencies:

```bash
uv sync
```

Check the CLI entrypoint:

```bash
uv run python cpa_warden.py --help
```

Run the minimum syntax validation:

```bash
uv run python -m py_compile cpa_warden.py
```

## Development Guidelines

- Do not put secrets in code, documentation examples, issue reports, or pull requests.
- If you change configuration keys, defaults, CLI behavior, or output files, update the relevant README sections, `config.example.json`, and `CHANGELOG.md` in the same change.
- Keep production terminal output concise. Put detailed troubleshooting information behind debug logging and the log file.
- If behavior changes, update the command examples and explanatory text that describe that behavior.
- If you change recovered-account or re-enable logic, document whether it relies on local state (for example `managed_reason`) or only on live scan signals.
- If you add new config keys, keep `config.example.json` complete and copy-ready, and update README explanations in the same change.
- External register hooks must stay as integration points only; do not add in-repo account registration logic.
- Avoid broad refactors unless they are necessary for the change being proposed.

## Documentation Expectations

- `README.md` and `README.zh-CN.md` should stay aligned in structure and meaning.
- CLI flags, default values, and output artifact names documented in Markdown must match the current source code.

## Validation Before PR

Before opening a pull request, run at least:

```bash
uv sync
uv run python -m py_compile cpa_warden.py
uv run python cpa_warden.py --help
```

If your change affects runtime behavior, validate it against your own CLIProxyAPI environment and sanitize all logs, exports, and screenshots before sharing them.

## Security And Sensitive Data Handling

Never commit:

- real `token` values
- real account identifiers unless explicitly required and fully sanitized
- local runtime artifacts such as `config.json`, SQLite databases, log files, or exported account JSON

If you need to share output for debugging, remove secrets and operational identifiers first.

For vulnerability reports, do not open a public issue. Follow [SECURITY.md](SECURITY.md) instead.

## Pull Request Expectations

- Describe the problem being solved.
- Summarize the behavior change clearly.
- Mention any config, logging, or export impact.
- Include the validation commands you ran.
- Keep example output sanitized.
