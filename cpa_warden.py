#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import asyncio
import getpass
import hashlib
import json
import logging
import math
import os
import shlex
import sqlite3
import sys
import urllib.parse
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

try:
    import aiohttp
except Exception:
    aiohttp = None

try:
    from rich.progress import (
        BarColumn,
        MofNCompleteColumn,
        Progress,
        SpinnerColumn,
        TextColumn,
        TimeElapsedColumn,
        TimeRemainingColumn,
    )
except Exception:
    Progress = None


DEFAULT_CONFIG_PATH = "config.json"
DEFAULT_TARGET_TYPE = "codex"
DEFAULT_USER_AGENT = "codex_cli_rs/0.76.0 (Debian 13.0.0; x86_64) WindowsTerminal"
DEFAULT_PROBE_WORKERS = 100
DEFAULT_ACTION_WORKERS = 100
DEFAULT_TIMEOUT = 15
DEFAULT_RETRIES = 3
DEFAULT_DELETE_RETRIES = 2
DEFAULT_QUOTA_ACTION = "disable"
DEFAULT_DELETE_401 = True
DEFAULT_AUTO_REENABLE = True
DEFAULT_REENABLE_SCOPE = "signal"
DEFAULT_DB_PATH = "cpa_warden_state.sqlite3"
DEFAULT_INVALID_OUTPUT = "cpa_warden_401_accounts.json"
DEFAULT_QUOTA_OUTPUT = "cpa_warden_quota_accounts.json"
DEFAULT_LOG_FILE = "cpa_warden.log"
DEFAULT_UPLOAD_WORKERS = 20
DEFAULT_UPLOAD_RETRIES = 2
DEFAULT_UPLOAD_METHOD = "json"
DEFAULT_UPLOAD_RECURSIVE = False
DEFAULT_UPLOAD_FORCE = False
DEFAULT_REFILL_STRATEGY = "to-threshold"
DEFAULT_AUTO_REGISTER = False
DEFAULT_REGISTER_TIMEOUT = 300
DEFAULT_QUOTA_DISABLE_THRESHOLD = 0.0
WHAM_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
SPARK_METERED_FEATURE = "codex_bengalfox"

AUTH_ACCOUNT_COLUMNS = [
    "name",
    "disabled",
    "id_token_json",
    "email",
    "provider",
    "source",
    "unavailable",
    "auth_index",
    "account",
    "type",
    "runtime_only",
    "status",
    "status_message",
    "chatgpt_account_id",
    "id_token_plan_type",
    "auth_updated_at",
    "auth_modtime",
    "auth_last_refresh",
    "api_http_status",
    "api_status_code",
    "usage_allowed",
    "usage_limit_reached",
    "usage_plan_type",
    "usage_email",
    "usage_reset_at",
    "usage_reset_after_seconds",
    "usage_spark_allowed",
    "usage_spark_limit_reached",
    "usage_spark_reset_at",
    "usage_spark_reset_after_seconds",
    "quota_signal_source",
    "is_invalid_401",
    "is_quota_limited",
    "is_recovered",
    "probe_error_kind",
    "probe_error_text",
    "managed_reason",
    "last_action",
    "last_action_status",
    "last_action_error",
    "last_seen_at",
    "last_probed_at",
    "updated_at",
]


LOGGER = logging.getLogger("cpa_warden")


def configure_logging(log_file: str, debug: bool) -> None:
    LOGGER.handlers.clear()
    LOGGER.setLevel(logging.DEBUG)
    LOGGER.propagate = False

    log_path = Path(log_file)
    if log_path.parent and str(log_path.parent) not in {"", "."}:
        log_path.parent.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.DEBUG if debug else logging.INFO)
    console_handler.setFormatter(formatter)
    LOGGER.addHandler(console_handler)

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    LOGGER.addHandler(file_handler)


def rich_progress_enabled(debug: bool) -> bool:
    return bool((Progress is not None) and (not debug) and hasattr(sys.stdout, "isatty") and sys.stdout.isatty())


class ProgressReporter:
    def __init__(self, description: str, total: int, *, debug: bool) -> None:
        self.description = description
        self.total = max(0, int(total))
        self.debug = debug
        self.enabled = rich_progress_enabled(debug)
        self._progress: Progress | None = None
        self._task_id: Any = None

    def __enter__(self) -> "ProgressReporter":
        if self.enabled:
            self._progress = Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                MofNCompleteColumn(),
                TimeElapsedColumn(),
                TimeRemainingColumn(),
                transient=False,
            )
            self._progress.start()
            self._task_id = self._progress.add_task(self.description, total=self.total)
        return self

    def advance(self, step: int = 1) -> None:
        if self._progress is not None and self._task_id is not None:
            self._progress.advance(self._task_id, step)

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self._progress is not None:
            self._progress.stop()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_json(resp: requests.Response) -> dict[str, Any]:
    try:
        data = resp.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def maybe_json_loads(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if not stripped:
        return None
    try:
        return json.loads(stripped)
    except Exception:
        return None


def compact_text(text: Any, limit: int = 240) -> str | None:
    if text is None:
        return None
    normalized = str(text).replace("\r", " ").replace("\n", " ").strip()
    if not normalized:
        return None
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(0, limit - 3)] + "..."


def normalize_optional_flag(value: Any) -> int | None:
    if isinstance(value, bool):
        return int(value)
    if value is None:
        return None
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return None
    return normalized if normalized in {0, 1} else None


def normalize_optional_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        normalized = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(normalized):
        return None
    return normalized


def normalize_optional_ratio(value: Any) -> float | None:
    normalized = normalize_optional_number(value)
    if normalized is None:
        return None
    if normalized < 0:
        return 0.0
    if normalized > 1:
        return 1.0
    return normalized


def retry_backoff_seconds(attempt: int) -> float:
    return min(3.0, 0.5 * (2 ** max(0, attempt)))


def pick_first_number(record: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        if key not in record:
            continue
        value = normalize_optional_number(record.get(key))
        if value is not None:
            return value
    return None


def extract_remaining_ratio(rate_limit: dict[str, Any] | None) -> float | None:
    if not isinstance(rate_limit, dict):
        return None

    total_keys = (
        "total",
        "limit",
        "max",
        "maximum",
        "quota",
        "request_limit",
        "requests_limit",
        "total_requests",
    )
    remaining_keys = (
        "remaining",
        "remaining_requests",
        "requests_remaining",
        "available",
        "available_requests",
        "left",
    )
    used_keys = (
        "used",
        "consumed",
        "used_requests",
        "requests_used",
        "spent",
    )

    windows: list[dict[str, Any]] = [rate_limit]
    for window_key in ("primary_window", "window", "current_window"):
        window = rate_limit.get(window_key)
        if isinstance(window, dict):
            windows.append(window)

    for window in windows:
        total = pick_first_number(window, total_keys)
        if total is None or total <= 0:
            continue
        remaining = pick_first_number(window, remaining_keys)
        used = pick_first_number(window, used_keys)
        if remaining is None and used is None:
            continue
        if remaining is not None:
            ratio = remaining / total
        else:
            ratio = (total - used) / total
        return normalize_optional_ratio(ratio)
    return None


def find_spark_rate_limit(parsed_body: dict[str, Any]) -> dict[str, Any] | None:
    additional_limits = parsed_body.get("additional_rate_limits")
    if not isinstance(additional_limits, list):
        return None

    spark_candidates: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for item in additional_limits:
        if not isinstance(item, dict):
            continue
        rate_limit = item.get("rate_limit")
        if not isinstance(rate_limit, dict):
            continue
        spark_candidates.append((item, rate_limit))

    for item, rate_limit in spark_candidates:
        metered_feature = str(item.get("metered_feature") or "").strip().lower()
        if metered_feature == SPARK_METERED_FEATURE:
            return rate_limit

    for item, rate_limit in spark_candidates:
        limit_name = str(item.get("limit_name") or "").strip().lower()
        if "spark" in limit_name:
            return rate_limit

    return None


def resolve_quota_signal(record: dict[str, Any]) -> tuple[int | None, int | None, str]:
    plan_type = str(record.get("usage_plan_type") or record.get("id_token_plan_type") or "").strip().lower()
    spark_limit_reached = normalize_optional_flag(record.get("usage_spark_limit_reached"))
    spark_allowed = normalize_optional_flag(record.get("usage_spark_allowed"))
    primary_limit_reached = normalize_optional_flag(record.get("usage_limit_reached"))
    primary_allowed = normalize_optional_flag(record.get("usage_allowed"))

    if plan_type == "pro" and spark_limit_reached is not None:
        effective_allowed = spark_allowed if spark_allowed is not None else primary_allowed
        return spark_limit_reached, effective_allowed, "spark"
    return primary_limit_reached, primary_allowed, "primary"


def resolve_quota_remaining_ratio(record: dict[str, Any]) -> tuple[float | None, str]:
    _, _, quota_signal_source = resolve_quota_signal(record)
    primary_ratio = normalize_optional_ratio(record.get("usage_remaining_ratio"))
    spark_ratio = normalize_optional_ratio(record.get("usage_spark_remaining_ratio"))

    if quota_signal_source == "spark":
        if spark_ratio is not None:
            return spark_ratio, "spark"
        if primary_ratio is not None:
            return primary_ratio, "primary_fallback"
        return None, "spark"

    if primary_ratio is not None:
        return primary_ratio, "primary"
    if spark_ratio is not None:
        return spark_ratio, "spark_fallback"
    return None, "primary"


def ensure_aiohttp() -> None:
    if aiohttp is None:
        print("错误: 未安装 aiohttp。请先执行 `uv sync`。", file=sys.stderr)
        sys.exit(1)


def get_item_name(item: dict[str, Any]) -> str:
    return str(item.get("name") or item.get("id") or "").strip()


def get_item_type(item: dict[str, Any]) -> str:
    return str(item.get("type") or item.get("typo") or "").strip()


def get_item_account(item: dict[str, Any]) -> str:
    return str(item.get("account") or item.get("email") or "").strip()


def get_id_token_object(item: dict[str, Any]) -> dict[str, Any]:
    parsed = maybe_json_loads(item.get("id_token"))
    return parsed if isinstance(parsed, dict) else {}


def extract_chatgpt_account_id_from_item(item: dict[str, Any]) -> str:
    id_token = get_id_token_object(item)
    for source in (id_token, item):
        for key in ("chatgpt_account_id", "chatgptAccountId", "account_id", "accountId"):
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def extract_id_token_plan_type(item: dict[str, Any]) -> str:
    id_token = get_id_token_object(item)
    value = id_token.get("plan_type")
    return value.strip() if isinstance(value, str) else ""


def mgmt_headers(token: str, include_json: bool = False) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json, text/plain, */*",
    }
    if include_json:
        headers["Content-Type"] = "application/json"
    return headers


def config_lookup(conf: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in conf and conf.get(key) not in (None, ""):
            return conf.get(key)
    return default


def parse_bool_config(value: Any, *, key: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        if value in {0, 1}:
            return bool(value)
        raise RuntimeError(f"{key} 必须是布尔值（true/false 或 1/0），当前={value!r}")
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off"}:
            return False
        raise RuntimeError(f"{key} 必须是布尔值（true/false 或 1/0），当前={value!r}")
    raise RuntimeError(f"{key} 必须是布尔值（true/false 或 1/0），当前类型={type(value).__name__}")


def load_config_json(path: str, required: bool = False) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        if required:
            raise RuntimeError(f"配置文件不存在: {path}")
        return {}

    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"读取配置文件失败: {exc}") from exc

    if not isinstance(data, dict):
        raise RuntimeError("配置文件格式错误: 顶层必须是 JSON 对象")

    return data


def build_settings(args: argparse.Namespace, conf: dict[str, Any]) -> dict[str, Any]:
    quota_action = str(
        args.quota_action
        or config_lookup(conf, "quota_action", default=DEFAULT_QUOTA_ACTION)
    ).strip().lower()
    if quota_action not in {"disable", "delete"}:
        raise RuntimeError("quota_action 只能是 disable 或 delete")

    upload_method = str(
        args.upload_method
        or config_lookup(conf, "upload_method", default=DEFAULT_UPLOAD_METHOD)
    ).strip().lower()
    if upload_method not in {"json", "multipart"}:
        raise RuntimeError("upload_method 只能是 json 或 multipart")

    refill_strategy = str(
        args.refill_strategy
        or config_lookup(conf, "refill_strategy", default=DEFAULT_REFILL_STRATEGY)
    ).strip().lower()
    if refill_strategy not in {"to-threshold", "fixed"}:
        raise RuntimeError("refill_strategy 只能是 to-threshold 或 fixed")

    reenable_scope = str(
        args.reenable_scope
        or config_lookup(conf, "reenable_scope", default=DEFAULT_REENABLE_SCOPE)
    ).strip().lower()
    if reenable_scope not in {"signal", "managed"}:
        raise RuntimeError("reenable_scope 只能是 signal 或 managed")

    settings = {
        "config_path": args.config,
        "base_url": str(config_lookup(conf, "base_url", default="")).strip(),
        "token": str(config_lookup(conf, "token", default="")).strip(),
        "mode": args.mode,
        "target_type": str(
            args.target_type
            or config_lookup(conf, "target_type", default=DEFAULT_TARGET_TYPE)
        ).strip(),
        "provider": str(
            args.provider if args.provider is not None else config_lookup(conf, "provider", default="")
        ).strip(),
        "probe_workers": int(
            args.probe_workers
            if args.probe_workers is not None
            else config_lookup(conf, "probe_workers", "workers", default=DEFAULT_PROBE_WORKERS)
        ),
        "action_workers": int(
            args.action_workers
            if args.action_workers is not None
            else config_lookup(conf, "action_workers", "delete_workers", default=DEFAULT_ACTION_WORKERS)
        ),
        "timeout": int(
            args.timeout if args.timeout is not None else config_lookup(conf, "timeout", default=DEFAULT_TIMEOUT)
        ),
        "retries": int(
            args.retries if args.retries is not None else config_lookup(conf, "retries", default=DEFAULT_RETRIES)
        ),
        "delete_retries": int(
            args.delete_retries
            if args.delete_retries is not None
            else config_lookup(conf, "delete_retries", "action_retries", default=DEFAULT_DELETE_RETRIES)
        ),
        "quota_action": quota_action,
        "quota_disable_threshold": float(
            args.quota_disable_threshold
            if args.quota_disable_threshold is not None
            else config_lookup(conf, "quota_disable_threshold", default=DEFAULT_QUOTA_DISABLE_THRESHOLD)
        ),
        "delete_401": (
            args.delete_401
            if args.delete_401 is not None
            else parse_bool_config(config_lookup(conf, "delete_401", default=DEFAULT_DELETE_401), key="delete_401")
        ),
        "auto_reenable": (
            args.auto_reenable
            if args.auto_reenable is not None
            else parse_bool_config(
                config_lookup(conf, "auto_reenable", default=DEFAULT_AUTO_REENABLE),
                key="auto_reenable",
            )
        ),
        "reenable_scope": reenable_scope,
        "db_path": str(
            args.db_path
            or config_lookup(conf, "db_path", default=DEFAULT_DB_PATH)
        ).strip(),
        "invalid_output": str(
            args.invalid_output
            or config_lookup(conf, "invalid_output", "output", default=DEFAULT_INVALID_OUTPUT)
        ).strip(),
        "quota_output": str(
            args.quota_output
            or config_lookup(conf, "quota_output", default=DEFAULT_QUOTA_OUTPUT)
        ).strip(),
        "log_file": str(
            args.log_file
            or config_lookup(conf, "log_file", default=DEFAULT_LOG_FILE)
        ).strip(),
        "user_agent": str(
            args.user_agent
            or config_lookup(conf, "user_agent", default=DEFAULT_USER_AGENT)
        ).strip(),
        "debug": bool(
            args.debug
            if args.debug is not None
            else parse_bool_config(config_lookup(conf, "debug", default=False), key="debug")
        ),
        "assume_yes": bool(args.yes),
        "upload_dir": str(
            args.upload_dir
            if args.upload_dir is not None
            else config_lookup(conf, "upload_dir", default="")
        ).strip(),
        "upload_workers": int(
            args.upload_workers
            if args.upload_workers is not None
            else config_lookup(conf, "upload_workers", default=DEFAULT_UPLOAD_WORKERS)
        ),
        "upload_retries": int(
            args.upload_retries
            if args.upload_retries is not None
            else config_lookup(conf, "upload_retries", default=DEFAULT_UPLOAD_RETRIES)
        ),
        "upload_method": upload_method,
        "upload_recursive": (
            args.upload_recursive
            if args.upload_recursive is not None
            else parse_bool_config(
                config_lookup(conf, "upload_recursive", default=DEFAULT_UPLOAD_RECURSIVE),
                key="upload_recursive",
            )
        ),
        "upload_force": (
            args.upload_force
            if args.upload_force is not None
            else parse_bool_config(config_lookup(conf, "upload_force", default=DEFAULT_UPLOAD_FORCE), key="upload_force")
        ),
        "min_valid_accounts": int(
            args.min_valid_accounts
            if args.min_valid_accounts is not None
            else config_lookup(conf, "min_valid_accounts", default=0)
        ),
        "refill_strategy": refill_strategy,
        "auto_register": (
            args.auto_register
            if args.auto_register is not None
            else parse_bool_config(config_lookup(conf, "auto_register", default=DEFAULT_AUTO_REGISTER), key="auto_register")
        ),
        "register_command": str(
            args.register_command
            if args.register_command is not None
            else config_lookup(conf, "register_command", default="")
        ).strip(),
        "register_timeout": int(
            args.register_timeout
            if args.register_timeout is not None
            else config_lookup(conf, "register_timeout", default=DEFAULT_REGISTER_TIMEOUT)
        ),
        "register_workdir": str(
            args.register_workdir
            if args.register_workdir is not None
            else config_lookup(conf, "register_workdir", default="")
        ).strip(),
    }

    if settings["probe_workers"] < 1:
        raise RuntimeError("probe_workers 必须 >= 1")
    if settings["action_workers"] < 1:
        raise RuntimeError("action_workers 必须 >= 1")
    if settings["timeout"] < 1:
        raise RuntimeError("timeout 必须 >= 1")
    if settings["retries"] < 0:
        raise RuntimeError("retries 不能小于 0")
    if settings["delete_retries"] < 0:
        raise RuntimeError("delete_retries 不能小于 0")
    if settings["quota_disable_threshold"] < 0 or settings["quota_disable_threshold"] > 1:
        raise RuntimeError("quota_disable_threshold 必须在 0 到 1 之间")
    if settings["upload_workers"] < 1:
        raise RuntimeError("upload_workers 必须 >= 1")
    if settings["upload_retries"] < 0:
        raise RuntimeError("upload_retries 不能小于 0")
    if settings["register_timeout"] < 1:
        raise RuntimeError("register_timeout 必须 >= 1")
    if not settings["target_type"]:
        raise RuntimeError("target_type 不能为空")
    if not settings["log_file"]:
        raise RuntimeError("log_file 不能为空")
    if settings["mode"] in {"upload", "maintain-refill"} and not settings["upload_dir"]:
        raise RuntimeError("upload/maintain-refill 模式下必须提供 upload_dir")
    if settings["mode"] == "maintain-refill":
        if settings["min_valid_accounts"] < 1:
            raise RuntimeError("maintain-refill 模式下 min_valid_accounts 必须 >= 1")
        if settings["auto_register"] and not settings["register_command"]:
            raise RuntimeError("auto_register=true 时必须提供 register_command")

    return settings


def connect_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS auth_accounts (
            name TEXT PRIMARY KEY,
            disabled INTEGER NOT NULL,
            id_token_json TEXT,
            email TEXT,
            provider TEXT,
            source TEXT,
            unavailable INTEGER NOT NULL,
            auth_index TEXT,
            account TEXT,
            type TEXT,
            runtime_only INTEGER NOT NULL,
            status TEXT,
            status_message TEXT,
            chatgpt_account_id TEXT,
            id_token_plan_type TEXT,
            auth_updated_at TEXT,
            auth_modtime TEXT,
            auth_last_refresh TEXT,
            api_http_status INTEGER,
            api_status_code INTEGER,
            usage_allowed INTEGER,
            usage_limit_reached INTEGER,
            usage_plan_type TEXT,
            usage_email TEXT,
            usage_reset_at INTEGER,
            usage_reset_after_seconds INTEGER,
            usage_spark_allowed INTEGER,
            usage_spark_limit_reached INTEGER,
            usage_spark_reset_at INTEGER,
            usage_spark_reset_after_seconds INTEGER,
            quota_signal_source TEXT,
            is_invalid_401 INTEGER NOT NULL DEFAULT 0,
            is_quota_limited INTEGER NOT NULL DEFAULT 0,
            is_recovered INTEGER NOT NULL DEFAULT 0,
            probe_error_kind TEXT,
            probe_error_text TEXT,
            managed_reason TEXT,
            last_action TEXT,
            last_action_status TEXT,
            last_action_error TEXT,
            last_seen_at TEXT NOT NULL,
            last_probed_at TEXT,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS scan_runs (
            run_id INTEGER PRIMARY KEY AUTOINCREMENT,
            mode TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            status TEXT NOT NULL,
            total_files INTEGER NOT NULL,
            filtered_files INTEGER NOT NULL,
            probed_files INTEGER NOT NULL,
            invalid_401_count INTEGER NOT NULL,
            quota_limited_count INTEGER NOT NULL,
            recovered_count INTEGER NOT NULL,
            delete_401 INTEGER NOT NULL,
            quota_action TEXT NOT NULL,
            probe_workers INTEGER NOT NULL,
            action_workers INTEGER NOT NULL,
            timeout_seconds INTEGER NOT NULL,
            retries INTEGER NOT NULL
        );
        """
    )
    ensure_auth_accounts_schema(conn)
    conn.commit()
    init_upload_db(conn)


def ensure_auth_accounts_schema(conn: sqlite3.Connection) -> None:
    required_columns = {
        "usage_spark_allowed": "INTEGER",
        "usage_spark_limit_reached": "INTEGER",
        "usage_spark_reset_at": "INTEGER",
        "usage_spark_reset_after_seconds": "INTEGER",
        "quota_signal_source": "TEXT",
    }
    rows = conn.execute("PRAGMA table_info(auth_accounts)").fetchall()
    existing_columns = {str(row[1]) for row in rows}
    added = False
    for column, column_type in required_columns.items():
        if column in existing_columns:
            continue
        conn.execute(f"ALTER TABLE auth_accounts ADD COLUMN {column} {column_type}")
        added = True
    if added:
        conn.commit()


def init_upload_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS auth_file_uploads (
            base_url TEXT NOT NULL,
            file_name TEXT NOT NULL,
            content_sha256 TEXT NOT NULL,
            file_path TEXT NOT NULL,
            file_size INTEGER NOT NULL,
            status TEXT NOT NULL,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            first_seen_at TEXT NOT NULL,
            last_attempt_at TEXT,
            uploaded_at TEXT,
            last_http_status INTEGER,
            last_error TEXT,
            last_response TEXT,
            PRIMARY KEY (base_url, file_name, content_sha256)
        );

        CREATE INDEX IF NOT EXISTS idx_auth_file_uploads_status
            ON auth_file_uploads(status);

        CREATE INDEX IF NOT EXISTS idx_auth_file_uploads_file_name
            ON auth_file_uploads(file_name);
        """
    )
    conn.commit()


def load_existing_state(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    rows = conn.execute("SELECT * FROM auth_accounts").fetchall()
    return {str(row["name"]): dict(row) for row in rows}


def start_scan_run(conn: sqlite3.Connection, settings: dict[str, Any]) -> int:
    cur = conn.execute(
        """
        INSERT INTO scan_runs (
            mode, started_at, finished_at, status, total_files, filtered_files, probed_files,
            invalid_401_count, quota_limited_count, recovered_count, delete_401, quota_action,
            probe_workers, action_workers, timeout_seconds, retries
        ) VALUES (?, ?, NULL, 'running', 0, 0, 0, 0, 0, 0, ?, ?, ?, ?, ?, ?)
        """,
        (
            settings["mode"],
            utc_now_iso(),
            int(bool(settings["delete_401"])),
            settings["quota_action"],
            settings["probe_workers"],
            settings["action_workers"],
            settings["timeout"],
            settings["retries"],
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def finish_scan_run(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    status: str,
    total_files: int,
    filtered_files: int,
    probed_files: int,
    invalid_401_count: int,
    quota_limited_count: int,
    recovered_count: int,
) -> None:
    conn.execute(
        """
        UPDATE scan_runs
        SET finished_at = ?, status = ?, total_files = ?, filtered_files = ?, probed_files = ?,
            invalid_401_count = ?, quota_limited_count = ?, recovered_count = ?
        WHERE run_id = ?
        """,
        (
            utc_now_iso(),
            status,
            int(total_files),
            int(filtered_files),
            int(probed_files),
            int(invalid_401_count),
            int(quota_limited_count),
            int(recovered_count),
            int(run_id),
        ),
    )
    conn.commit()


def upsert_auth_accounts(conn: sqlite3.Connection, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    columns_sql = ", ".join(AUTH_ACCOUNT_COLUMNS)
    placeholders = ", ".join(f":{column}" for column in AUTH_ACCOUNT_COLUMNS)
    updates = ", ".join(
        f"{column} = excluded.{column}"
        for column in AUTH_ACCOUNT_COLUMNS
        if column != "name"
    )
    conn.executemany(
        f"""
        INSERT INTO auth_accounts ({columns_sql})
        VALUES ({placeholders})
        ON CONFLICT(name) DO UPDATE SET
            {updates}
        """,
        rows,
    )
    conn.commit()


def row_to_bool(value: Any) -> bool:
    return bool(int(value)) if value is not None else False


def build_auth_record(
    item: dict[str, Any],
    existing_row: dict[str, Any] | None,
    now_iso: str,
) -> dict[str, Any]:
    id_token_obj = get_id_token_object(item)
    id_token_json = json.dumps(id_token_obj, ensure_ascii=False) if id_token_obj else None
    existing_row = existing_row or {}
    return {
        "name": get_item_name(item),
        "disabled": int(bool(item.get("disabled"))),
        "id_token_json": id_token_json,
        "email": str(item.get("email") or "").strip() or None,
        "provider": str(item.get("provider") or "").strip() or None,
        "source": str(item.get("source") or "").strip() or None,
        "unavailable": int(bool(item.get("unavailable"))),
        "auth_index": str(item.get("auth_index") or "").strip() or None,
        "account": get_item_account(item) or None,
        "type": get_item_type(item) or None,
        "runtime_only": int(bool(item.get("runtime_only"))),
        "status": str(item.get("status") or "").strip() or None,
        "status_message": compact_text(item.get("status_message"), 1200),
        "chatgpt_account_id": extract_chatgpt_account_id_from_item(item) or None,
        "id_token_plan_type": extract_id_token_plan_type(item) or None,
        "auth_updated_at": str(item.get("updated_at") or "").strip() or None,
        "auth_modtime": str(item.get("modtime") or "").strip() or None,
        "auth_last_refresh": str(item.get("last_refresh") or "").strip() or None,
        "api_http_status": None,
        "api_status_code": None,
        "usage_allowed": None,
        "usage_limit_reached": None,
        "usage_plan_type": None,
        "usage_email": None,
        "usage_reset_at": None,
        "usage_reset_after_seconds": None,
        "usage_spark_allowed": None,
        "usage_spark_limit_reached": None,
        "usage_spark_reset_at": None,
        "usage_spark_reset_after_seconds": None,
        "quota_signal_source": None,
        "is_invalid_401": 0,
        "is_quota_limited": 0,
        "is_recovered": 0,
        "probe_error_kind": None,
        "probe_error_text": None,
        "managed_reason": existing_row.get("managed_reason"),
        "last_action": existing_row.get("last_action"),
        "last_action_status": existing_row.get("last_action_status"),
        "last_action_error": existing_row.get("last_action_error"),
        "last_seen_at": now_iso,
        "last_probed_at": None,
        "updated_at": now_iso,
    }


def matches_filters(record: dict[str, Any], target_type: str, provider: str) -> bool:
    if str(record.get("type") or "").lower() != target_type.lower():
        return False
    if provider and str(record.get("provider") or "").lower() != provider.lower():
        return False
    return True


def fetch_auth_files(base_url: str, token: str, timeout: int) -> list[dict[str, Any]]:
    LOGGER.info("开始拉取 auth-files 列表")
    LOGGER.debug("GET %s/v0/management/auth-files", base_url.rstrip("/"))
    resp = requests.get(
        f"{base_url.rstrip('/')}/v0/management/auth-files",
        headers=mgmt_headers(token),
        timeout=timeout,
    )
    resp.raise_for_status()
    data = safe_json(resp)
    files = data.get("files")
    LOGGER.info("auth-files 拉取完成: %s", len(files) if isinstance(files, list) else 0)
    return files if isinstance(files, list) else []


def discover_upload_files(upload_dir: str, recursive: bool) -> list[Path]:
    root = Path(upload_dir).expanduser()
    if not root.exists():
        raise RuntimeError(f"upload_dir 不存在: {upload_dir}")
    if not root.is_dir():
        raise RuntimeError(f"upload_dir 不是目录: {upload_dir}")

    iterator = root.rglob("*.json") if recursive else root.glob("*.json")
    files = [path for path in iterator if path.is_file() and path.name.endswith(".json")]
    return sorted(files, key=lambda p: str(p))


def validate_and_digest_json_file(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except Exception as exc:
        raise RuntimeError(f"文件不是 UTF-8 编码: {path}") from exc

    try:
        json.loads(text)
    except Exception as exc:
        raise RuntimeError(f"JSON 格式无效: {path}") from exc

    return {
        "file_name": path.name,
        "file_path": str(path),
        "content_text": text,
        "content_bytes": raw,
        "content_sha256": hashlib.sha256(raw).hexdigest(),
        "file_size": len(raw),
    }


def select_upload_candidates(
    validated_files: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in validated_files:
        grouped[str(item["file_name"])].append(item)

    selected: list[dict[str, Any]] = []
    skipped_local_duplicates: list[dict[str, Any]] = []
    conflicting_names: dict[str, list[dict[str, Any]]] = {}

    for file_name in sorted(grouped.keys()):
        items = sorted(grouped[file_name], key=lambda row: str(row["file_path"]))
        hashes = {str(row["content_sha256"]) for row in items}
        if len(hashes) > 1:
            conflicting_names[file_name] = items
            continue
        selected.append(items[0])
        for duplicated in items[1:]:
            skipped_local_duplicates.append(
                {
                    "file_name": duplicated["file_name"],
                    "file_path": duplicated["file_path"],
                    "status_code": None,
                    "ok": True,
                    "outcome": "skipped_local_duplicate",
                    "error": "local duplicate with same file name and content",
                    "error_kind": None,
                }
            )

    return selected, skipped_local_duplicates, conflicting_names


def fetch_remote_auth_file_names(base_url: str, token: str, timeout: int) -> set[str]:
    files = fetch_auth_files(base_url, token, timeout)
    names = set()
    for item in files:
        name = get_item_name(item)
        if name:
            names.add(name)
    return names


def mark_upload_skipped_remote_exists(
    conn: sqlite3.Connection,
    *,
    base_url: str,
    file_name: str,
    content_sha256: str,
    file_path: str,
    file_size: int,
) -> None:
    now_iso = utc_now_iso()
    conn.execute(
        """
        INSERT INTO auth_file_uploads (
            base_url, file_name, content_sha256, file_path, file_size, status,
            attempt_count, first_seen_at, last_attempt_at, uploaded_at, last_http_status, last_error, last_response
        ) VALUES (?, ?, ?, ?, ?, 'skipped_remote_exists', 0, ?, ?, ?, NULL, NULL, NULL)
        ON CONFLICT(base_url, file_name, content_sha256) DO UPDATE SET
            file_path = excluded.file_path,
            file_size = excluded.file_size,
            status = 'skipped_remote_exists',
            last_attempt_at = excluded.last_attempt_at,
            uploaded_at = excluded.uploaded_at,
            last_http_status = NULL,
            last_error = NULL,
            last_response = NULL
        """,
        (
            base_url,
            file_name,
            content_sha256,
            file_path,
            int(file_size),
            now_iso,
            now_iso,
            now_iso,
        ),
    )
    conn.commit()


def claim_upload_slot(
    conn: sqlite3.Connection,
    *,
    base_url: str,
    file_name: str,
    content_sha256: str,
    file_path: str,
    file_size: int,
) -> str:
    now_iso = utc_now_iso()
    cur = conn.execute(
        """
        INSERT INTO auth_file_uploads (
            base_url, file_name, content_sha256, file_path, file_size, status,
            attempt_count, first_seen_at, last_attempt_at, uploaded_at, last_http_status, last_error, last_response
        ) VALUES (?, ?, ?, ?, ?, 'uploading', 0, ?, NULL, NULL, NULL, NULL, NULL)
        ON CONFLICT(base_url, file_name, content_sha256) DO NOTHING
        """,
        (base_url, file_name, content_sha256, file_path, int(file_size), now_iso),
    )
    conn.commit()
    if cur.rowcount == 1:
        return "claimed_new"

    row = conn.execute(
        """
        SELECT status
        FROM auth_file_uploads
        WHERE base_url = ? AND file_name = ? AND content_sha256 = ?
        """,
        (base_url, file_name, content_sha256),
    ).fetchone()
    if row is None:
        return "claimed_new"

    status = str(row["status"] or "").strip()
    if status in {"success", "skipped_remote_exists"}:
        return "skipped_done"
    if status == "uploading":
        return "skipped_in_progress"
    if status == "failed":
        takeover = conn.execute(
            """
            UPDATE auth_file_uploads
            SET status = 'uploading',
                file_path = ?,
                file_size = ?,
                last_error = NULL,
                last_response = NULL,
                last_http_status = NULL
            WHERE base_url = ? AND file_name = ? AND content_sha256 = ? AND status = 'failed'
            """,
            (file_path, int(file_size), base_url, file_name, content_sha256),
        )
        conn.commit()
        if takeover.rowcount == 1:
            return "claimed_retry"
        return "skipped_in_progress"
    return "skipped_done"


def mark_upload_attempt(
    conn: sqlite3.Connection,
    *,
    base_url: str,
    file_name: str,
    content_sha256: str,
) -> None:
    conn.execute(
        """
        UPDATE auth_file_uploads
        SET attempt_count = attempt_count + 1,
            last_attempt_at = ?,
            status = 'uploading'
        WHERE base_url = ? AND file_name = ? AND content_sha256 = ?
        """,
        (utc_now_iso(), base_url, file_name, content_sha256),
    )
    conn.commit()


def mark_upload_success(
    conn: sqlite3.Connection,
    *,
    base_url: str,
    file_name: str,
    content_sha256: str,
    http_status: int | None,
    response_text: str | None,
) -> None:
    conn.execute(
        """
        UPDATE auth_file_uploads
        SET status = 'success',
            uploaded_at = ?,
            last_http_status = ?,
            last_error = NULL,
            last_response = ?
        WHERE base_url = ? AND file_name = ? AND content_sha256 = ?
        """,
        (
            utc_now_iso(),
            http_status,
            compact_text(response_text, 2000),
            base_url,
            file_name,
            content_sha256,
        ),
    )
    conn.commit()


def mark_upload_failure(
    conn: sqlite3.Connection,
    *,
    base_url: str,
    file_name: str,
    content_sha256: str,
    http_status: int | None,
    error_text: str,
    response_text: str | None,
) -> None:
    conn.execute(
        """
        UPDATE auth_file_uploads
        SET status = 'failed',
            uploaded_at = NULL,
            last_http_status = ?,
            last_error = ?,
            last_response = ?
        WHERE base_url = ? AND file_name = ? AND content_sha256 = ?
        """,
        (
            http_status,
            compact_text(error_text, 600),
            compact_text(response_text, 2000),
            base_url,
            file_name,
            content_sha256,
        ),
    )
    conn.commit()


async def upload_auth_file_async(
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
    conn: sqlite3.Connection,
    settings: dict[str, Any],
    candidate: dict[str, Any],
    stop_event: asyncio.Event,
) -> dict[str, Any]:
    base_url = settings["base_url"]
    token = settings["token"]
    timeout = settings["timeout"]
    upload_retries = settings["upload_retries"]
    upload_method = settings["upload_method"]
    file_name = str(candidate["file_name"])
    file_path = str(candidate["file_path"])
    content_sha256 = str(candidate["content_sha256"])
    file_size = int(candidate["file_size"])

    if stop_event.is_set():
        return {
            "file_name": file_name,
            "file_path": file_path,
            "status_code": None,
            "ok": False,
            "outcome": "skipped_due_to_stop",
            "error": "stopped: core auth manager unavailable",
            "error_kind": "core_auth_manager_unavailable",
        }

    claim_state = claim_upload_slot(
        conn,
        base_url=base_url,
        file_name=file_name,
        content_sha256=content_sha256,
        file_path=file_path,
        file_size=file_size,
    )
    if claim_state == "skipped_done":
        return {
            "file_name": file_name,
            "file_path": file_path,
            "status_code": None,
            "ok": True,
            "outcome": "skipped_already_uploaded",
            "error": None,
            "error_kind": None,
        }
    if claim_state == "skipped_in_progress":
        return {
            "file_name": file_name,
            "file_path": file_path,
            "status_code": None,
            "ok": True,
            "outcome": "skipped_in_progress",
            "error": None,
            "error_kind": None,
        }

    if stop_event.is_set():
        mark_upload_failure(
            conn,
            base_url=base_url,
            file_name=file_name,
            content_sha256=content_sha256,
            http_status=None,
            error_text="stopped: core auth manager unavailable",
            response_text=None,
        )
        return {
            "file_name": file_name,
            "file_path": file_path,
            "status_code": None,
            "ok": False,
            "outcome": "skipped_due_to_stop",
            "error": "stopped: core auth manager unavailable",
            "error_kind": "core_auth_manager_unavailable",
        }

    encoded_name = urllib.parse.quote(file_name, safe="")
    json_upload_url = f"{base_url.rstrip('/')}/v0/management/auth-files?name={encoded_name}"
    multipart_upload_url = f"{base_url.rstrip('/')}/v0/management/auth-files"

    for attempt in range(upload_retries + 1):
        mark_upload_attempt(
            conn,
            base_url=base_url,
            file_name=file_name,
            content_sha256=content_sha256,
        )
        try:
            async with semaphore:
                if stop_event.is_set():
                    mark_upload_failure(
                        conn,
                        base_url=base_url,
                        file_name=file_name,
                        content_sha256=content_sha256,
                        http_status=None,
                        error_text="stopped: core auth manager unavailable",
                        response_text=None,
                    )
                    return {
                        "file_name": file_name,
                        "file_path": file_path,
                        "status_code": None,
                        "ok": False,
                        "outcome": "skipped_due_to_stop",
                        "error": "stopped: core auth manager unavailable",
                        "error_kind": "core_auth_manager_unavailable",
                    }

                if upload_method == "multipart":
                    form = aiohttp.FormData()
                    form.add_field(
                        "file",
                        candidate["content_bytes"],
                        filename=file_name,
                        content_type="application/json",
                    )
                    req_headers = mgmt_headers(token)
                    request_kwargs: dict[str, Any] = {"data": form}
                    request_url = multipart_upload_url
                else:
                    req_headers = mgmt_headers(token, include_json=True)
                    request_kwargs = {"data": candidate["content_text"]}
                    request_url = json_upload_url

                async with session.post(
                    request_url,
                    headers=req_headers,
                    timeout=timeout,
                    **request_kwargs,
                ) as resp:
                    text = await resp.text()
                    data = maybe_json_loads(text)
                    ok = resp.status == 200 and isinstance(data, dict) and data.get("status") == "ok"
                    if ok:
                        mark_upload_success(
                            conn,
                            base_url=base_url,
                            file_name=file_name,
                            content_sha256=content_sha256,
                            http_status=resp.status,
                            response_text=text,
                        )
                        return {
                            "file_name": file_name,
                            "file_path": file_path,
                            "status_code": resp.status,
                            "ok": True,
                            "outcome": "uploaded_success",
                            "error": None,
                            "error_kind": None,
                        }

                    resp_error = ""
                    if isinstance(data, dict):
                        resp_error = str(data.get("error") or "").strip()
                    is_core_unavailable = resp.status == 503 and (
                        resp_error == "core auth manager unavailable"
                        or "core auth manager unavailable" in str(text).lower()
                    )
                    if is_core_unavailable:
                        stop_event.set()
                        mark_upload_failure(
                            conn,
                            base_url=base_url,
                            file_name=file_name,
                            content_sha256=content_sha256,
                            http_status=resp.status,
                            error_text="core auth manager unavailable",
                            response_text=text,
                        )
                        return {
                            "file_name": file_name,
                            "file_path": file_path,
                            "status_code": resp.status,
                            "ok": False,
                            "outcome": "upload_failed",
                            "error": "core auth manager unavailable",
                            "error_kind": "core_auth_manager_unavailable",
                        }

                    should_retry = resp.status == 429 or resp.status >= 500
                    error_text = resp_error or compact_text(text, 240) or f"http {resp.status}"
                    if should_retry and attempt < upload_retries:
                        await asyncio.sleep(0.5 * (2**attempt))
                        continue

                    mark_upload_failure(
                        conn,
                        base_url=base_url,
                        file_name=file_name,
                        content_sha256=content_sha256,
                        http_status=resp.status,
                        error_text=error_text,
                        response_text=text,
                    )
                    return {
                        "file_name": file_name,
                        "file_path": file_path,
                        "status_code": resp.status,
                        "ok": False,
                        "outcome": "upload_failed",
                        "error": error_text,
                        "error_kind": None,
                    }
        except asyncio.TimeoutError:
            if attempt < upload_retries:
                await asyncio.sleep(0.5 * (2**attempt))
                continue
            mark_upload_failure(
                conn,
                base_url=base_url,
                file_name=file_name,
                content_sha256=content_sha256,
                http_status=None,
                error_text="timeout",
                response_text=None,
            )
            return {
                "file_name": file_name,
                "file_path": file_path,
                "status_code": None,
                "ok": False,
                "outcome": "upload_failed",
                "error": "timeout",
                "error_kind": "timeout",
            }
        except Exception as exc:
            if attempt < upload_retries:
                await asyncio.sleep(0.5 * (2**attempt))
                continue
            mark_upload_failure(
                conn,
                base_url=base_url,
                file_name=file_name,
                content_sha256=content_sha256,
                http_status=None,
                error_text=str(exc),
                response_text=None,
            )
            return {
                "file_name": file_name,
                "file_path": file_path,
                "status_code": None,
                "ok": False,
                "outcome": "upload_failed",
                "error": str(exc),
                "error_kind": "other",
            }

    mark_upload_failure(
        conn,
        base_url=base_url,
        file_name=file_name,
        content_sha256=content_sha256,
        http_status=None,
        error_text="upload failed after retries",
        response_text=None,
    )
    return {
        "file_name": file_name,
        "file_path": file_path,
        "status_code": None,
        "ok": False,
        "outcome": "upload_failed",
        "error": "upload failed after retries",
        "error_kind": None,
    }


def summarize_upload_results(
    results: list[dict[str, Any]],
    *,
    discovered_count: int,
    selected_count: int,
    to_upload_count: int,
) -> None:
    counter = Counter(str(row.get("outcome") or "unknown") for row in results)
    LOGGER.info("上传扫描文件数: %s", discovered_count)
    LOGGER.info("上传候选文件数: %s", selected_count)
    LOGGER.info("需要实际上传数: %s", to_upload_count)
    LOGGER.info("上传成功: %s", counter.get("uploaded_success", 0))
    LOGGER.info("跳过(已上传): %s", counter.get("skipped_already_uploaded", 0))
    LOGGER.info("跳过(远端已存在): %s", counter.get("skipped_remote_exists", 0))
    LOGGER.info("跳过(进行中): %s", counter.get("skipped_in_progress", 0))
    LOGGER.info("跳过(本地重复): %s", counter.get("skipped_local_duplicate", 0))
    LOGGER.info("校验失败: %s", counter.get("validation_failed", 0))
    LOGGER.info("上传失败: %s", counter.get("upload_failed", 0))

    failed = [row for row in results if row.get("outcome") in {"validation_failed", "upload_failed"}]
    for row in failed[:10]:
        LOGGER.warning(
            "[上传失败] %s | status=%s | %s",
            row.get("file_name") or row.get("file_path"),
            row.get("status_code"),
            compact_text(row.get("error"), 200) or "-",
        )


async def run_upload_async(
    conn: sqlite3.Connection,
    settings: dict[str, Any],
    *,
    limit: int | None = None,
) -> dict[str, Any]:
    LOGGER.info(
        "开始上传认证文件: dir=%s recursive=%s workers=%s retries=%s method=%s force=%s limit=%s",
        settings["upload_dir"],
        settings["upload_recursive"],
        settings["upload_workers"],
        settings["upload_retries"],
        settings["upload_method"],
        settings["upload_force"],
        limit if limit is not None else "all",
    )
    discovered_paths = discover_upload_files(settings["upload_dir"], settings["upload_recursive"])
    if not discovered_paths:
        LOGGER.info("未发现可上传的 .json 文件")
        return {"results": []}

    validated_files: list[dict[str, Any]] = []
    pre_results: list[dict[str, Any]] = []
    for path in discovered_paths:
        try:
            validated_files.append(validate_and_digest_json_file(path))
        except Exception as exc:
            pre_results.append(
                {
                    "file_name": path.name,
                    "file_path": str(path),
                    "status_code": None,
                    "ok": False,
                    "outcome": "validation_failed",
                    "error": str(exc),
                    "error_kind": "validation",
                }
            )

    selected_candidates, skipped_local_duplicates, conflicting_names = select_upload_candidates(validated_files)
    pre_results.extend(skipped_local_duplicates)
    if conflicting_names:
        samples: list[str] = []
        for name, rows in list(sorted(conflicting_names.items(), key=lambda item: item[0]))[:10]:
            samples.append(f"{name}({len(rows)} paths)")
        raise RuntimeError(
            "检测到同名不同内容的本地文件，已停止上传: " + ", ".join(samples)
        )
    if not selected_candidates:
        summarize_upload_results(
            pre_results,
            discovered_count=len(discovered_paths),
            selected_count=0,
            to_upload_count=0,
        )
        failed_count = sum(
            1 for row in pre_results if row.get("outcome") in {"validation_failed", "upload_failed"}
        )
        if failed_count > 0:
            raise RuntimeError(f"上传完成但存在失败文件: {failed_count}")
        LOGGER.info("上传流程完成")
        return {"results": pre_results}

    remote_names: set[str] = set()
    if not settings["upload_force"]:
        remote_names = fetch_remote_auth_file_names(settings["base_url"], settings["token"], settings["timeout"])
    upload_candidates: list[dict[str, Any]] = []
    for candidate in selected_candidates:
        if (not settings["upload_force"]) and candidate["file_name"] in remote_names:
            mark_upload_skipped_remote_exists(
                conn,
                base_url=settings["base_url"],
                file_name=candidate["file_name"],
                content_sha256=candidate["content_sha256"],
                file_path=candidate["file_path"],
                file_size=candidate["file_size"],
            )
            pre_results.append(
                {
                    "file_name": candidate["file_name"],
                    "file_path": candidate["file_path"],
                    "status_code": None,
                    "ok": True,
                    "outcome": "skipped_remote_exists",
                    "error": None,
                    "error_kind": None,
                }
            )
            continue
        upload_candidates.append(candidate)

    if limit is not None:
        if limit < 0:
            raise RuntimeError("upload limit 不能小于 0")
        upload_candidates = upload_candidates[:limit]

    if not upload_candidates:
        results = list(pre_results)
        summarize_upload_results(
            results,
            discovered_count=len(discovered_paths),
            selected_count=len(selected_candidates),
            to_upload_count=0,
        )
        failed_count = sum(1 for row in results if row.get("outcome") in {"validation_failed", "upload_failed"})
        if failed_count > 0:
            raise RuntimeError(f"上传完成但存在失败文件: {failed_count}")
        LOGGER.info("上传流程完成")
        return {"results": results}

    connector = aiohttp.TCPConnector(
        limit=max(1, settings["upload_workers"]),
        limit_per_host=max(1, settings["upload_workers"]),
    )
    client_timeout = aiohttp.ClientTimeout(total=max(1, settings["timeout"]))
    semaphore = asyncio.Semaphore(max(1, settings["upload_workers"]))
    stop_event = asyncio.Event()

    results = list(pre_results)
    async with aiohttp.ClientSession(connector=connector, timeout=client_timeout, trust_env=True) as session:
        tasks = [
            asyncio.create_task(
                upload_auth_file_async(
                    session,
                    semaphore,
                    conn,
                    settings,
                    candidate,
                    stop_event,
                )
            )
            for candidate in upload_candidates
        ]
        done = 0
        total = len(tasks)
        next_report = 100
        with ProgressReporter("上传认证文件", total, debug=settings["debug"]) as progress:
            for task in asyncio.as_completed(tasks):
                results.append(await task)
                done += 1
                progress.advance()
                if (not progress.enabled) and (done >= next_report or done == total):
                    LOGGER.info("上传进度: %s/%s", done, total)
                    next_report += 100

    summarize_upload_results(
        results,
        discovered_count=len(discovered_paths),
        selected_count=len(selected_candidates),
        to_upload_count=len(upload_candidates),
    )

    if any(row.get("error_kind") == "core_auth_manager_unavailable" for row in results):
        raise RuntimeError("core auth manager unavailable")
    failed_count = sum(1 for row in results if row.get("outcome") in {"validation_failed", "upload_failed"})
    if failed_count > 0:
        raise RuntimeError(f"上传完成但存在失败文件: {failed_count}")

    LOGGER.info("上传流程完成")
    return {"results": results}


def build_wham_usage_payload(auth_index: str, user_agent: str, chatgpt_account_id: str) -> dict[str, Any]:
    return {
        "authIndex": auth_index,
        "method": "GET",
        "url": WHAM_USAGE_URL,
        "header": {
            "Authorization": "Bearer $TOKEN$",
            "Content-Type": "application/json",
            "User-Agent": user_agent,
            "Chatgpt-Account-Id": chatgpt_account_id,
        },
    }


async def probe_wham_usage_async(
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
    base_url: str,
    token: str,
    record: dict[str, Any],
    timeout: int,
    retries: int,
    user_agent: str,
) -> dict[str, Any]:
    result = dict(record)
    result["last_probed_at"] = utc_now_iso()
    LOGGER.debug(
        "开始探测账号: name=%s auth_index=%s unavailable=%s disabled=%s has_account_id=%s",
        result.get("name"),
        result.get("auth_index"),
        bool(result.get("unavailable")),
        bool(result.get("disabled")),
        "yes" if result.get("chatgpt_account_id") else "no",
    )

    auth_index = str(result.get("auth_index") or "").strip()
    account_id = str(result.get("chatgpt_account_id") or "").strip()

    if not auth_index:
        result["probe_error_kind"] = "missing_auth_index"
        result["probe_error_text"] = "missing auth_index"
        LOGGER.debug("跳过账号: name=%s reason=missing_auth_index", result.get("name"))
        return result

    if not account_id:
        result["probe_error_kind"] = "missing_chatgpt_account_id"
        result["probe_error_text"] = "missing Chatgpt-Account-Id"
        LOGGER.debug("跳过账号: name=%s reason=missing_chatgpt_account_id", result.get("name"))
        return result

    payload = build_wham_usage_payload(auth_index, user_agent, account_id)
    url = f"{base_url.rstrip('/')}/v0/management/api-call"

    for attempt in range(retries + 1):
        try:
            async with semaphore:
                async with session.post(
                    url,
                    headers=mgmt_headers(token, include_json=True),
                    json=payload,
                    timeout=timeout,
                ) as resp:
                    text = await resp.text()
                    result["api_http_status"] = resp.status

                    if resp.status == 429:
                        result["probe_error_kind"] = "management_api_http_429"
                        result["probe_error_text"] = "management api-call http 429"
                        if attempt < retries:
                            delay = retry_backoff_seconds(attempt)
                            LOGGER.debug(
                                "探测受限重试: name=%s api_http=429 attempt=%s/%s delay=%.2fs",
                                result.get("name"),
                                attempt + 1,
                                retries + 1,
                                delay,
                            )
                            await asyncio.sleep(delay)
                            continue
                        LOGGER.debug("探测失败: name=%s api_http=429 retries_exhausted=true", result.get("name"))
                        return result
                    if resp.status >= 500:
                        result["probe_error_kind"] = "management_api_http_5xx"
                        result["probe_error_text"] = f"management api-call http {resp.status}"
                        if attempt < retries:
                            delay = retry_backoff_seconds(attempt)
                            LOGGER.debug(
                                "探测服务端错误重试: name=%s api_http=%s attempt=%s/%s delay=%.2fs",
                                result.get("name"),
                                resp.status,
                                attempt + 1,
                                retries + 1,
                                delay,
                            )
                            await asyncio.sleep(delay)
                            continue
                        LOGGER.debug(
                            "探测失败: name=%s api_http=%s retries_exhausted=true",
                            result.get("name"),
                            resp.status,
                        )
                        return result
                    if resp.status >= 400:
                        result["probe_error_kind"] = "management_api_http_4xx"
                        result["probe_error_text"] = f"management api-call http {resp.status}"
                        LOGGER.debug("探测失败: name=%s api_http=%s", result.get("name"), resp.status)
                        return result

                    try:
                        outer = json.loads(text)
                    except Exception:
                        result["probe_error_kind"] = "api_call_invalid_json"
                        result["probe_error_text"] = "api-call response is not valid JSON"
                        LOGGER.debug("探测失败: name=%s reason=api_call_invalid_json", result.get("name"))
                        return result

                    if not isinstance(outer, dict):
                        result["probe_error_kind"] = "api_call_not_object"
                        result["probe_error_text"] = f"api-call response is not JSON object: {type(outer).__name__}"
                        LOGGER.debug("探测失败: name=%s reason=api_call_not_object", result.get("name"))
                        return result

                    status_code = outer.get("status_code")
                    result["api_status_code"] = status_code
                    if status_code is None:
                        result["probe_error_kind"] = "missing_status_code"
                        result["probe_error_text"] = "missing status_code in api-call response"
                        LOGGER.debug("探测失败: name=%s reason=missing_status_code", result.get("name"))
                        return result

                    if status_code == 401:
                        result["probe_error_kind"] = None
                        result["probe_error_text"] = None
                        LOGGER.debug("探测完成: name=%s status_code=401", result.get("name"))
                        return result

                    body = outer.get("body")
                    if isinstance(body, dict):
                        parsed_body = body
                    elif isinstance(body, str):
                        try:
                            parsed_body = json.loads(body)
                        except Exception:
                            result["probe_error_kind"] = "body_invalid_json"
                            result["probe_error_text"] = "api-call body is not valid JSON"
                            LOGGER.debug("探测失败: name=%s reason=body_invalid_json", result.get("name"))
                            return result
                    elif body is None:
                        parsed_body = {}
                    else:
                        result["probe_error_kind"] = "body_not_object"
                        result["probe_error_text"] = f"api-call body is not JSON object: {type(body).__name__}"
                        LOGGER.debug("探测失败: name=%s reason=body_not_object", result.get("name"))
                        return result

                    if parsed_body and not isinstance(parsed_body, dict):
                        result["probe_error_kind"] = "body_not_object"
                        result["probe_error_text"] = f"api-call body is not JSON object: {type(parsed_body).__name__}"
                        LOGGER.debug("探测失败: name=%s reason=body_not_object", result.get("name"))
                        return result

                    rate_limit = parsed_body.get("rate_limit") if isinstance(parsed_body, dict) else None
                    primary_window = rate_limit.get("primary_window") if isinstance(rate_limit, dict) else None
                    result["usage_allowed"] = (
                        int(rate_limit.get("allowed"))
                        if isinstance(rate_limit, dict) and isinstance(rate_limit.get("allowed"), bool)
                        else None
                    )
                    result["usage_limit_reached"] = (
                        int(rate_limit.get("limit_reached"))
                        if isinstance(rate_limit, dict) and isinstance(rate_limit.get("limit_reached"), bool)
                        else None
                    )
                    result["usage_remaining_ratio"] = extract_remaining_ratio(rate_limit)
                    result["usage_plan_type"] = (
                        str(parsed_body.get("plan_type") or "").strip() or None
                        if isinstance(parsed_body, dict)
                        else None
                    )
                    result["usage_email"] = (
                        str(parsed_body.get("email") or "").strip() or None
                        if isinstance(parsed_body, dict)
                        else None
                    )
                    result["usage_reset_at"] = (
                        int(primary_window.get("reset_at"))
                        if isinstance(primary_window, dict) and primary_window.get("reset_at") is not None
                        else None
                    )
                    result["usage_reset_after_seconds"] = (
                        int(primary_window.get("reset_after_seconds"))
                        if isinstance(primary_window, dict) and primary_window.get("reset_after_seconds") is not None
                        else None
                    )
                    spark_rate_limit = find_spark_rate_limit(parsed_body) if isinstance(parsed_body, dict) else None
                    spark_primary_window = spark_rate_limit.get("primary_window") if isinstance(spark_rate_limit, dict) else None
                    result["usage_spark_allowed"] = (
                        int(spark_rate_limit.get("allowed"))
                        if isinstance(spark_rate_limit, dict) and isinstance(spark_rate_limit.get("allowed"), bool)
                        else None
                    )
                    result["usage_spark_limit_reached"] = (
                        int(spark_rate_limit.get("limit_reached"))
                        if isinstance(spark_rate_limit, dict) and isinstance(spark_rate_limit.get("limit_reached"), bool)
                        else None
                    )
                    result["usage_spark_remaining_ratio"] = extract_remaining_ratio(spark_rate_limit)
                    result["usage_spark_reset_at"] = (
                        int(spark_primary_window.get("reset_at"))
                        if isinstance(spark_primary_window, dict) and spark_primary_window.get("reset_at") is not None
                        else None
                    )
                    result["usage_spark_reset_after_seconds"] = (
                        int(spark_primary_window.get("reset_after_seconds"))
                        if isinstance(spark_primary_window, dict) and spark_primary_window.get("reset_after_seconds") is not None
                        else None
                    )
                    effective_limit_reached, effective_allowed, quota_signal_source = resolve_quota_signal(result)
                    result["quota_signal_source"] = quota_signal_source

                    if status_code == 200:
                        result["probe_error_kind"] = None
                        result["probe_error_text"] = None
                        LOGGER.debug(
                            "探测完成: name=%s api_http=%s status_code=%s limit_reached=%s allowed=%s source=%s remaining_ratio=%s spark_limit_reached=%s spark_remaining_ratio=%s",
                            result.get("name"),
                            result.get("api_http_status"),
                            result.get("api_status_code"),
                            effective_limit_reached,
                            effective_allowed,
                            result.get("quota_signal_source"),
                            result.get("usage_remaining_ratio"),
                            result.get("usage_spark_limit_reached"),
                            result.get("usage_spark_remaining_ratio"),
                        )
                        return result

                    result["probe_error_kind"] = "other"
                    result["probe_error_text"] = f"unexpected upstream status_code={status_code}"
                    LOGGER.debug(
                        "探测异常: name=%s api_http=%s status_code=%s",
                        result.get("name"),
                        result.get("api_http_status"),
                        status_code,
                    )
                    return result
        except asyncio.TimeoutError:
            result["probe_error_kind"] = "timeout"
            result["probe_error_text"] = "timeout"
            LOGGER.debug("探测超时: name=%s attempt=%s", result.get("name"), attempt + 1)
        except Exception as exc:
            result["probe_error_kind"] = "other"
            result["probe_error_text"] = str(exc)
            LOGGER.debug("探测异常: name=%s error=%s", result.get("name"), exc)

        if attempt >= retries:
            return result

    return result


def classify_account_state(record: dict[str, Any], *, quota_disable_threshold: float) -> dict[str, Any]:
    invalid_401 = bool(record.get("unavailable")) or record.get("api_status_code") == 401
    effective_limit_reached, effective_allowed, quota_signal_source = resolve_quota_signal(record)
    effective_remaining_ratio, remaining_ratio_source = resolve_quota_remaining_ratio(record)
    quota_limited_by_threshold = (
        quota_disable_threshold > 0
        and effective_remaining_ratio is not None
        and effective_remaining_ratio <= quota_disable_threshold
    )
    quota_limited = (
        not invalid_401
        and not bool(record.get("unavailable"))
        and record.get("api_status_code") == 200
        and (effective_limit_reached == 1 or quota_limited_by_threshold)
    )
    recovered = (
        not invalid_401
        and not quota_limited
        and bool(record.get("disabled"))
        # Recovery is based on live usage signals only, so different CPA instances
        # with independent local DBs can still converge on the same re-enable set.
        and record.get("api_status_code") == 200
        and effective_allowed == 1
        and effective_limit_reached == 0
    )

    record["quota_signal_source"] = quota_signal_source
    record["quota_remaining_ratio"] = effective_remaining_ratio
    record["quota_remaining_ratio_source"] = remaining_ratio_source
    record["quota_threshold_triggered"] = int(quota_limited_by_threshold)
    record["is_invalid_401"] = int(invalid_401)
    record["is_quota_limited"] = int(quota_limited)
    record["is_recovered"] = int(recovered)
    record["updated_at"] = utc_now_iso()
    if quota_limited_by_threshold and effective_limit_reached != 1:
        LOGGER.info(
            "限额阈值触发: name=%s remaining_ratio=%.4f threshold=%.4f signal_source=%s ratio_source=%s",
            record.get("name"),
            effective_remaining_ratio,
            quota_disable_threshold,
            quota_signal_source,
            remaining_ratio_source,
        )
    return record


async def probe_accounts_async(
    records: list[dict[str, Any]],
    *,
    base_url: str,
    token: str,
    timeout: int,
    retries: int,
    user_agent: str,
    probe_workers: int,
    quota_disable_threshold: float,
    debug: bool,
) -> list[dict[str, Any]]:
    if not records:
        return []

    LOGGER.info(
        "开始并发探测 wham/usage: candidates=%s workers=%s timeout=%ss retries=%s",
        len(records),
        probe_workers,
        timeout,
        retries,
    )

    connector = aiohttp.TCPConnector(limit=max(1, probe_workers), limit_per_host=max(1, probe_workers))
    client_timeout = aiohttp.ClientTimeout(total=max(1, timeout))
    semaphore = asyncio.Semaphore(max(1, probe_workers))

    results: list[dict[str, Any]] = []
    async with aiohttp.ClientSession(connector=connector, timeout=client_timeout, trust_env=True) as session:
        tasks = [
            asyncio.create_task(
                probe_wham_usage_async(
                    session,
                    semaphore,
                    base_url,
                    token,
                    record,
                    timeout,
                    retries,
                    user_agent,
                )
            )
            for record in records
        ]

        done = 0
        total = len(tasks)
        next_report = 100
        with ProgressReporter("探测账号", total, debug=debug) as progress:
            for task in asyncio.as_completed(tasks):
                probed = classify_account_state(await task, quota_disable_threshold=quota_disable_threshold)
                results.append(probed)
                done += 1
                progress.advance()
                if (not progress.enabled) and (done >= next_report or done == total):
                    LOGGER.info("探测进度: %s/%s", done, total)
                    next_report += 100

    return results


def build_invalid_export_record(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": record.get("name"),
        "account": record.get("account") or record.get("email") or "",
        "email": record.get("email") or "",
        "provider": record.get("provider"),
        "source": record.get("source"),
        "disabled": bool(record.get("disabled")),
        "unavailable": bool(record.get("unavailable")),
        "auth_index": record.get("auth_index"),
        "chatgpt_account_id": record.get("chatgpt_account_id"),
        "api_http_status": record.get("api_http_status"),
        "api_status_code": record.get("api_status_code"),
        "status": record.get("status"),
        "status_message": record.get("status_message"),
        "probe_error_kind": record.get("probe_error_kind"),
        "probe_error_text": record.get("probe_error_text"),
    }


def build_quota_export_record(record: dict[str, Any]) -> dict[str, Any]:
    effective_limit_reached, effective_allowed, quota_signal_source = resolve_quota_signal(record)
    effective_remaining_ratio, remaining_ratio_source = resolve_quota_remaining_ratio(record)
    return {
        "name": record.get("name"),
        "account": record.get("account") or record.get("email") or "",
        "email": record.get("usage_email") or record.get("email") or "",
        "provider": record.get("provider"),
        "source": record.get("source"),
        "disabled": bool(record.get("disabled")),
        "unavailable": bool(record.get("unavailable")),
        "auth_index": record.get("auth_index"),
        "chatgpt_account_id": record.get("chatgpt_account_id"),
        "api_http_status": record.get("api_http_status"),
        "api_status_code": record.get("api_status_code"),
        "limit_reached": bool(effective_limit_reached) if effective_limit_reached is not None else None,
        "allowed": bool(effective_allowed) if effective_allowed is not None else None,
        "quota_signal_source": record.get("quota_signal_source") or quota_signal_source,
        "remaining_ratio": effective_remaining_ratio,
        "remaining_ratio_source": record.get("quota_remaining_ratio_source") or remaining_ratio_source,
        "threshold_triggered": bool(record.get("quota_threshold_triggered")),
        "primary_remaining_ratio": normalize_optional_ratio(record.get("usage_remaining_ratio")),
        "spark_remaining_ratio": normalize_optional_ratio(record.get("usage_spark_remaining_ratio")),
        "primary_limit_reached": bool(record.get("usage_limit_reached")) if record.get("usage_limit_reached") is not None else None,
        "primary_allowed": bool(record.get("usage_allowed")) if record.get("usage_allowed") is not None else None,
        "spark_limit_reached": bool(record.get("usage_spark_limit_reached")) if record.get("usage_spark_limit_reached") is not None else None,
        "spark_allowed": bool(record.get("usage_spark_allowed")) if record.get("usage_spark_allowed") is not None else None,
        "plan_type": record.get("usage_plan_type") or record.get("id_token_plan_type"),
        "reset_at": record.get("usage_reset_at"),
        "reset_after_seconds": record.get("usage_reset_after_seconds"),
        "spark_reset_at": record.get("usage_spark_reset_at"),
        "spark_reset_after_seconds": record.get("usage_spark_reset_after_seconds"),
        "probe_error_kind": record.get("probe_error_kind"),
        "probe_error_text": record.get("probe_error_text"),
    }


def export_records(path: str, rows: list[dict[str, Any]]) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, ensure_ascii=False, indent=2)


def summarize_failures(records: list[dict[str, Any]], sample_limit: int = 3) -> None:
    failed = [row for row in records if row.get("probe_error_kind")]
    if not failed:
        return

    labels = {
        "missing_auth_index": "缺少 auth_index",
        "missing_chatgpt_account_id": "缺少 Chatgpt-Account-Id",
        "management_api_http_4xx": "管理接口 HTTP 4xx",
        "management_api_http_429": "管理接口 HTTP 429",
        "management_api_http_5xx": "管理接口 HTTP 5xx",
        "api_call_invalid_json": "api-call 返回不是 JSON",
        "api_call_not_object": "api-call 返回不是对象",
        "missing_status_code": "api-call 缺少 status_code",
        "body_invalid_json": "api-call body 不是合法 JSON",
        "body_not_object": "api-call body 不是对象",
        "timeout": "请求超时",
        "other": "其他异常",
    }
    buckets: dict[str, dict[str, Any]] = defaultdict(lambda: {"count": 0, "samples": []})

    for row in failed:
        key = str(row.get("probe_error_kind") or "other")
        bucket = buckets[key]
        bucket["count"] += 1
        if len(bucket["samples"]) < sample_limit:
            bucket["samples"].append(
                " | ".join(
                    [
                        row.get("name") or "-",
                        f"account={row.get('account') or row.get('email') or '-'}",
                        f"auth_index={row.get('auth_index') or '-'}",
                        f"has_account_id={'yes' if row.get('chatgpt_account_id') else 'no'}",
                        f"api_http={row.get('api_http_status') if row.get('api_http_status') is not None else '-'}",
                        f"status_code={row.get('api_status_code') if row.get('api_status_code') is not None else '-'}",
                        f"error_kind={key}",
                        f"error={compact_text(row.get('probe_error_text'), 100) or '-'}",
                    ]
                )
            )

    LOGGER.info("失败原因统计:")
    for key, payload in sorted(buckets.items(), key=lambda item: (-item[1]["count"], item[0])):
        LOGGER.info("  - %s: %s", labels.get(key, key), payload["count"])

    LOGGER.debug("失败样例:")
    for key, payload in sorted(buckets.items(), key=lambda item: (-item[1]["count"], item[0])):
        LOGGER.debug("  [%s]", labels.get(key, key))
        for sample in payload["samples"]:
            LOGGER.debug("    %s", sample)


async def delete_account_async(
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
    base_url: str,
    token: str,
    name: str,
    timeout: int,
    delete_retries: int,
) -> dict[str, Any]:
    encoded_name = urllib.parse.quote(name, safe="")
    url = f"{base_url.rstrip('/')}/v0/management/auth-files?name={encoded_name}"
    max_attempts = max(1, int(delete_retries) + 1)
    for attempt in range(max_attempts):
        try:
            async with semaphore:
                async with session.delete(url, headers=mgmt_headers(token), timeout=timeout) as resp:
                    text = await resp.text()
                    data = maybe_json_loads(text)
                    ok = resp.status == 200 and isinstance(data, dict) and data.get("status") == "ok"
                    if ok:
                        return {
                            "name": name,
                            "ok": True,
                            "status_code": resp.status,
                            "error": None,
                            "attempts": attempt + 1,
                        }

                    should_retry = resp.status in {408, 425, 429} or resp.status >= 500
                    if should_retry and attempt < (max_attempts - 1):
                        wait_seconds = min(5.0, 0.5 * (2**attempt))
                        LOGGER.debug(
                            "删除失败准备重试: name=%s status=%s attempt=%s/%s wait=%.1fs",
                            name,
                            resp.status,
                            attempt + 1,
                            max_attempts,
                            wait_seconds,
                        )
                        await asyncio.sleep(wait_seconds)
                        continue

                    return {
                        "name": name,
                        "ok": False,
                        "status_code": resp.status,
                        "error": compact_text(text, 200),
                        "attempts": attempt + 1,
                    }
        except Exception as exc:
            if attempt < (max_attempts - 1):
                wait_seconds = min(5.0, 0.5 * (2**attempt))
                LOGGER.debug(
                    "删除异常准备重试: name=%s attempt=%s/%s error=%s wait=%.1fs",
                    name,
                    attempt + 1,
                    max_attempts,
                    exc,
                    wait_seconds,
                )
                await asyncio.sleep(wait_seconds)
                continue

            return {"name": name, "ok": False, "status_code": None, "error": str(exc), "attempts": attempt + 1}

    return {"name": name, "ok": False, "status_code": None, "error": "unreachable", "attempts": 0}


async def set_account_disabled_async(
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
    base_url: str,
    token: str,
    name: str,
    disabled: bool,
    timeout: int,
) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}/v0/management/auth-files/status"
    payload = {"name": name, "disabled": bool(disabled)}
    try:
        async with semaphore:
            async with session.patch(
                url,
                headers=mgmt_headers(token, include_json=True),
                json=payload,
                timeout=timeout,
            ) as resp:
                text = await resp.text()
                data = maybe_json_loads(text)
                ok = resp.status == 200 and isinstance(data, dict) and data.get("status") == "ok"
                return {
                    "name": name,
                    "ok": ok,
                    "disabled": bool(disabled),
                    "status_code": resp.status,
                    "error": None if ok else compact_text(text, 200),
                }
    except Exception as exc:
        return {
            "name": name,
            "ok": False,
            "disabled": bool(disabled),
            "status_code": None,
            "error": str(exc),
        }


async def run_action_group_async(
    *,
    base_url: str,
    token: str,
    timeout: int,
    workers: int,
    items: list[str],
    fn_name: str,
    disabled: bool | None = None,
    delete_retries: int = 0,
    debug: bool = False,
) -> list[dict[str, Any]]:
    if not items:
        return []

    connector = aiohttp.TCPConnector(limit=max(1, workers), limit_per_host=max(1, workers))
    client_timeout = aiohttp.ClientTimeout(total=max(1, timeout))
    semaphore = asyncio.Semaphore(max(1, workers))

    async with aiohttp.ClientSession(connector=connector, timeout=client_timeout, trust_env=True) as session:
        tasks = []
        for name in items:
            if fn_name == "delete":
                tasks.append(
                    asyncio.create_task(
                        delete_account_async(
                            session,
                            semaphore,
                            base_url,
                            token,
                            name,
                            timeout,
                            delete_retries=max(0, int(delete_retries)),
                        )
                    )
                )
            else:
                tasks.append(
                    asyncio.create_task(
                        set_account_disabled_async(
                            session,
                            semaphore,
                            base_url,
                            token,
                            name,
                            bool(disabled),
                            timeout,
                        )
                    )
                )

        results: list[dict[str, Any]] = []
        done = 0
        total = len(tasks)
        next_report = 100
        action_label = "删除" if fn_name == "delete" else ("禁用" if disabled else "启用")
        with ProgressReporter(f"{action_label}账号", total, debug=debug) as progress:
            for task in asyncio.as_completed(tasks):
                results.append(await task)
                done += 1
                progress.advance()
                if (not progress.enabled) and (done >= next_report or done == total):
                    LOGGER.info("%s进度: %s/%s", action_label, done, total)
                    next_report += 100
        return results


def apply_action_results(
    records_by_name: dict[str, dict[str, Any]],
    results: list[dict[str, Any]],
    *,
    action: str,
    managed_reason_on_success: str | None,
    disabled_value: int | None,
) -> list[dict[str, Any]]:
    updated: list[dict[str, Any]] = []
    now_iso = utc_now_iso()
    for result in results:
        name = result.get("name")
        record = records_by_name.get(name)
        if not record:
            continue
        record["last_action"] = action
        record["last_action_status"] = "success" if result.get("ok") else "failed"
        record["last_action_error"] = result.get("error")
        record["updated_at"] = now_iso
        if result.get("ok"):
            if managed_reason_on_success is None:
                record["managed_reason"] = None
            else:
                record["managed_reason"] = managed_reason_on_success
            if disabled_value is not None:
                record["disabled"] = disabled_value
            LOGGER.debug("动作成功: action=%s name=%s", action, name)
        else:
            LOGGER.debug(
                "动作失败: action=%s name=%s status_code=%s error=%s",
                action,
                name,
                result.get("status_code"),
                compact_text(result.get("error"), 200),
            )
        updated.append(record)
    return updated


def mark_quota_already_disabled(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    now_iso = utc_now_iso()
    updated = []
    for record in records:
        record["managed_reason"] = "quota_disabled"
        record["last_action"] = "mark_quota_disabled"
        record["last_action_status"] = "success"
        record["last_action_error"] = None
        record["updated_at"] = now_iso
        LOGGER.debug("标记已禁用限额账号: name=%s", record.get("name"))
        updated.append(record)
    return updated


def print_scan_summary(
    *,
    total_files: int,
    candidate_records: list[dict[str, Any]],
    invalid_records: list[dict[str, Any]],
    quota_records: list[dict[str, Any]],
    recovered_records: list[dict[str, Any]],
) -> None:
    status_counter = Counter(str(row.get("status") or "") for row in candidate_records)
    LOGGER.info("总认证文件数: %s", total_files)
    LOGGER.info("符合过滤条件账号数: %s", len(candidate_records))
    LOGGER.info("401 账号数: %s", len(invalid_records))
    LOGGER.info("限额账号数: %s", len(quota_records))
    LOGGER.info("恢复候选账号数: %s", len(recovered_records))
    LOGGER.debug("状态分布: %s", dict(sorted(status_counter.items(), key=lambda item: item[0])))


def summarize_action_results(label: str, results: list[dict[str, Any]]) -> None:
    if not results:
        LOGGER.info("%s: 0", label)
        return
    success = [row for row in results if row.get("ok")]
    failed = [row for row in results if not row.get("ok")]
    LOGGER.info("%s: 成功=%s，失败=%s", label, len(success), len(failed))
    for row in failed[:10]:
        LOGGER.warning("[%s失败] %s | %s", label, row.get("name"), compact_text(row.get("error"), 160) or "-")


def confirm_action(message: str, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        LOGGER.warning("缺少交互终端，已取消: %s", message)
        return False
    answer = input(f"{message}，输入 DELETE 确认: ").strip()
    return answer == "DELETE"


def export_current_results(
    invalid_output: str,
    quota_output: str,
    invalid_records: list[dict[str, Any]],
    quota_records: list[dict[str, Any]],
) -> None:
    export_records(invalid_output, [build_invalid_export_record(row) for row in invalid_records])
    export_records(quota_output, [build_quota_export_record(row) for row in quota_records])
    LOGGER.info("已导出 401 列表: %s", invalid_output)
    LOGGER.info("已导出限额列表: %s", quota_output)


async def run_scan_async(conn: sqlite3.Connection, settings: dict[str, Any]) -> dict[str, Any]:
    run_id = start_scan_run(conn, settings)
    LOGGER.info(
        "开始扫描: mode=%s db=%s log=%s quota_disable_threshold=%s",
        settings["mode"],
        settings["db_path"],
        settings["log_file"],
        settings["quota_disable_threshold"],
    )
    try:
        now_iso = utc_now_iso()
        files = fetch_auth_files(settings["base_url"], settings["token"], settings["timeout"])
        existing_state = load_existing_state(conn)

        inventory_records = []
        for item in files:
            name = get_item_name(item)
            if not name:
                continue
            inventory_records.append(build_auth_record(item, existing_state.get(name), now_iso))

        upsert_auth_accounts(conn, inventory_records)

        candidate_records = [
            record
            for record in inventory_records
            if matches_filters(record, settings["target_type"], settings["provider"])
        ]
        probed_records = await probe_accounts_async(
            candidate_records,
            base_url=settings["base_url"],
            token=settings["token"],
            timeout=settings["timeout"],
            retries=settings["retries"],
            user_agent=settings["user_agent"],
            probe_workers=settings["probe_workers"],
            quota_disable_threshold=settings["quota_disable_threshold"],
            debug=settings["debug"],
        )
        probed_map = {record["name"]: record for record in probed_records}

        for record in inventory_records:
            if record["name"] in probed_map:
                record.update(probed_map[record["name"]])
                record["updated_at"] = utc_now_iso()

        upsert_auth_accounts(conn, inventory_records)

        current_candidates = [record for record in inventory_records if matches_filters(record, settings["target_type"], settings["provider"])]
        invalid_records = [row for row in current_candidates if row.get("is_invalid_401") == 1]
        quota_records = [row for row in current_candidates if row.get("is_quota_limited") == 1]
        recovered_records = [row for row in current_candidates if row.get("is_recovered") == 1]
        failure_records = [row for row in current_candidates if row.get("probe_error_kind")]
        probed_files = sum(1 for row in current_candidates if row.get("last_probed_at"))

        finish_scan_run(
            conn,
            run_id,
            status="success",
            total_files=len(files),
            filtered_files=len(current_candidates),
            probed_files=probed_files,
            invalid_401_count=len(invalid_records),
            quota_limited_count=len(quota_records),
            recovered_count=len(recovered_records),
        )

        print_scan_summary(
            total_files=len(files),
            candidate_records=current_candidates,
            invalid_records=invalid_records,
            quota_records=quota_records,
            recovered_records=recovered_records,
        )
        if failure_records:
            summarize_failures(failure_records)
        export_current_results(settings["invalid_output"], settings["quota_output"], invalid_records, quota_records)
        LOGGER.info("扫描完成")

        return {
            "run_id": run_id,
            "all_records": inventory_records,
            "candidate_records": current_candidates,
            "invalid_records": invalid_records,
            "quota_records": quota_records,
            "recovered_records": recovered_records,
        }
    except Exception:
        finish_scan_run(
            conn,
            run_id,
            status="failed",
            total_files=0,
            filtered_files=0,
            probed_files=0,
            invalid_401_count=0,
            quota_limited_count=0,
            recovered_count=0,
        )
        raise


async def run_maintain_async(conn: sqlite3.Connection, settings: dict[str, Any]) -> dict[str, Any]:
    LOGGER.info(
        "开始维护: delete_401=%s quota_action=%s quota_disable_threshold=%s auto_reenable=%s reenable_scope=%s delete_retries=%s",
        settings["delete_401"],
        settings["quota_action"],
        settings["quota_disable_threshold"],
        settings["auto_reenable"],
        settings["reenable_scope"],
        settings["delete_retries"],
    )
    scan_result = await run_scan_async(conn, settings)

    records_by_name = {
        row["name"]: row
        for row in scan_result["candidate_records"]
        if row.get("name")
    }
    invalid_records = scan_result["invalid_records"]
    quota_records = [row for row in scan_result["quota_records"] if row.get("is_invalid_401") != 1]
    recovered_records = scan_result["recovered_records"]

    delete_401_results: list[dict[str, Any]] = []
    quota_action_results: list[dict[str, Any]] = []
    reenable_results: list[dict[str, Any]] = []

    if settings["delete_401"] and invalid_records:
        names = [row["name"] for row in invalid_records if row.get("name")]
        LOGGER.info("待删除 401 账号: %s", len(names))
        if confirm_action(f"即将删除 {len(names)} 个 401 账号", settings["assume_yes"]):
            delete_401_results = await run_action_group_async(
                base_url=settings["base_url"],
                token=settings["token"],
                timeout=settings["timeout"],
                workers=settings["action_workers"],
                items=names,
                fn_name="delete",
                delete_retries=settings["delete_retries"],
                debug=settings["debug"],
            )
            updated = apply_action_results(
                records_by_name,
                delete_401_results,
                action="delete_401",
                managed_reason_on_success="deleted_401",
                disabled_value=None,
            )
            upsert_auth_accounts(conn, updated)

    deleted_401_names = {row["name"] for row in delete_401_results if row.get("ok")}

    if settings["quota_action"] == "disable":
        already_disabled = [row for row in quota_records if row.get("name") not in deleted_401_names and row.get("disabled") == 1]
        to_disable = [row for row in quota_records if row.get("name") not in deleted_401_names and row.get("disabled") != 1]
        LOGGER.info("待禁用限额账号: %s", len(to_disable))
        LOGGER.debug("已处于禁用状态的限额账号: %s", len(already_disabled))

        if already_disabled:
            upsert_auth_accounts(conn, mark_quota_already_disabled(already_disabled))

        if to_disable:
            quota_action_results = await run_action_group_async(
                base_url=settings["base_url"],
                token=settings["token"],
                timeout=settings["timeout"],
                workers=settings["action_workers"],
                items=[row["name"] for row in to_disable if row.get("name")],
                fn_name="toggle",
                disabled=True,
                debug=settings["debug"],
            )
            updated = apply_action_results(
                records_by_name,
                quota_action_results,
                action="disable_quota",
                managed_reason_on_success="quota_disabled",
                disabled_value=1,
            )
            upsert_auth_accounts(conn, updated)
    else:
        quota_delete_targets = [row["name"] for row in quota_records if row.get("name") and row.get("name") not in deleted_401_names]
        LOGGER.info("待删除限额账号: %s", len(quota_delete_targets))
        if quota_delete_targets and confirm_action(f"即将删除 {len(quota_delete_targets)} 个限额账号", settings["assume_yes"]):
            quota_action_results = await run_action_group_async(
                base_url=settings["base_url"],
                token=settings["token"],
                timeout=settings["timeout"],
                workers=settings["action_workers"],
                items=quota_delete_targets,
                fn_name="delete",
                delete_retries=settings["delete_retries"],
                debug=settings["debug"],
            )
            updated = apply_action_results(
                records_by_name,
                quota_action_results,
                action="delete_quota",
                managed_reason_on_success="quota_deleted",
                disabled_value=None,
            )
            upsert_auth_accounts(conn, updated)

    deleted_quota_names = {row["name"] for row in quota_action_results if row.get("ok") and settings["quota_action"] == "delete"}

    if settings["auto_reenable"]:
        recoverable_records = (
            recovered_records
            if settings["reenable_scope"] == "signal"
            else [row for row in recovered_records if str(row.get("managed_reason") or "") == "quota_disabled"]
        )
        reenable_targets = [
            row["name"]
            for row in recoverable_records
            if row.get("name") not in deleted_401_names and row.get("name") not in deleted_quota_names
        ]
        LOGGER.info("待恢复启用账号: %s（scope=%s）", len(reenable_targets), settings["reenable_scope"])
        if reenable_targets:
            reenable_results = await run_action_group_async(
                base_url=settings["base_url"],
                token=settings["token"],
                timeout=settings["timeout"],
                workers=settings["action_workers"],
                items=reenable_targets,
                fn_name="toggle",
                disabled=False,
                debug=settings["debug"],
            )
            updated = apply_action_results(
                records_by_name,
                reenable_results,
                action="reenable_quota",
                managed_reason_on_success=None,
                disabled_value=0,
            )
            upsert_auth_accounts(conn, updated)

    summarize_action_results("删除 401", delete_401_results)
    summarize_action_results("处理限额", quota_action_results)
    summarize_action_results("恢复启用", reenable_results)
    LOGGER.info("维护完成")

    return {
        "scan": scan_result,
        "delete_401_results": delete_401_results,
        "quota_action_results": quota_action_results,
        "reenable_results": reenable_results,
    }


def count_valid_accounts(records: list[dict[str, Any]]) -> int:
    valid = 0
    for row in records:
        if row_to_bool(row.get("disabled")):
            continue
        if int(row.get("is_invalid_401") or 0) == 1:
            continue
        if int(row.get("is_quota_limited") or 0) == 1:
            continue
        if row.get("probe_error_kind"):
            continue
        valid += 1
    return valid


def compute_refill_upload_count(valid_count: int, min_valid_accounts: int, strategy: str) -> int:
    if valid_count >= min_valid_accounts:
        return 0
    gap = min_valid_accounts - valid_count
    if strategy == "fixed":
        return min_valid_accounts
    return gap


async def run_register_hook_async(settings: dict[str, Any], *, count: int) -> dict[str, Any]:
    if count <= 0:
        return {"executed": False, "requested_count": 0, "new_files": 0}

    command = str(settings.get("register_command") or "").strip()
    if not command:
        raise RuntimeError("register_command 为空，无法执行 auto_register")

    upload_dir = Path(str(settings["upload_dir"])).expanduser()
    upload_dir.mkdir(parents=True, exist_ok=True)
    before_files = {
        str(path)
        for path in discover_upload_files(str(upload_dir), bool(settings["upload_recursive"]))
    }

    cwd = str(settings.get("register_workdir") or "").strip()
    cwd_path = Path(cwd).expanduser() if cwd else None
    if cwd_path is not None and (not cwd_path.exists() or not cwd_path.is_dir()):
        raise RuntimeError(f"register_workdir 不存在或不是目录: {cwd}")

    cmd = (
        f"{command} "
        f"--count {shlex.quote(str(count))} "
        f"--output-dir {shlex.quote(str(upload_dir))}"
    )
    env = os.environ.copy()
    env["CPA_REGISTER_COUNT"] = str(count)
    env["CPA_REGISTER_OUTPUT_DIR"] = str(upload_dir)

    LOGGER.info("执行外部注册钩子: count=%s command=%s", count, command)
    process = await asyncio.create_subprocess_shell(
        cmd,
        cwd=str(cwd_path) if cwd_path is not None else None,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(),
            timeout=int(settings["register_timeout"]),
        )
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        raise RuntimeError(f"register command timeout after {settings['register_timeout']}s")

    stdout_text = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
    stderr_text = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""
    if process.returncode != 0:
        raise RuntimeError(
            f"register command failed: exit={process.returncode}, stderr={compact_text(stderr_text, 240) or '-'}"
        )

    after_files = {
        str(path)
        for path in discover_upload_files(str(upload_dir), bool(settings["upload_recursive"]))
    }
    new_files = max(0, len(after_files - before_files))
    if new_files < 1:
        raise RuntimeError("register command succeeded but produced no new .json files")

    LOGGER.info("外部注册钩子完成: 新增文件=%s", new_files)
    if stdout_text.strip():
        LOGGER.debug("register stdout: %s", compact_text(stdout_text, 800))
    if stderr_text.strip():
        LOGGER.debug("register stderr: %s", compact_text(stderr_text, 800))

    return {
        "executed": True,
        "requested_count": int(count),
        "new_files": int(new_files),
        "return_code": int(process.returncode),
    }


async def run_maintain_refill_async(conn: sqlite3.Connection, settings: dict[str, Any]) -> dict[str, Any]:
    min_valid_accounts = int(settings["min_valid_accounts"])
    refill_strategy = str(settings["refill_strategy"])

    LOGGER.info(
        "开始维护补充: min_valid_accounts=%s refill_strategy=%s auto_register=%s",
        min_valid_accounts,
        refill_strategy,
        bool(settings["auto_register"]),
    )
    maintain_result = await run_maintain_async(conn, settings)

    after_maintain_scan = await run_scan_async(conn, settings)
    valid_after_maintain = count_valid_accounts(after_maintain_scan["candidate_records"])
    LOGGER.info("维护后有效账号数: %s (目标 >= %s)", valid_after_maintain, min_valid_accounts)

    refill_upload_result: dict[str, Any] | None = None
    after_refill_scan = after_maintain_scan
    valid_after_refill = valid_after_maintain
    refill_upload_count = compute_refill_upload_count(valid_after_maintain, min_valid_accounts, refill_strategy)

    if refill_upload_count > 0:
        LOGGER.info("触发补充上传: count=%s strategy=%s", refill_upload_count, refill_strategy)
        refill_upload_result = await run_upload_async(conn, settings, limit=refill_upload_count)
        after_refill_scan = await run_scan_async(conn, settings)
        valid_after_refill = count_valid_accounts(after_refill_scan["candidate_records"])
        LOGGER.info("补充上传后有效账号数: %s (目标 >= %s)", valid_after_refill, min_valid_accounts)

    register_result: dict[str, Any] | None = None
    register_upload_result: dict[str, Any] | None = None
    final_scan = after_refill_scan
    final_valid_count = valid_after_refill
    if final_valid_count < min_valid_accounts:
        if not settings["auto_register"]:
            raise RuntimeError(
                f"维护补充后有效账号不足: valid={final_valid_count}, min_required={min_valid_accounts}"
            )

        register_count = min_valid_accounts - final_valid_count
        register_result = await run_register_hook_async(settings, count=register_count)
        register_upload_result = await run_upload_async(conn, settings, limit=register_count)
        final_scan = await run_scan_async(conn, settings)
        final_valid_count = count_valid_accounts(final_scan["candidate_records"])
        LOGGER.info("注册并上传后有效账号数: %s (目标 >= %s)", final_valid_count, min_valid_accounts)

    if final_valid_count < min_valid_accounts:
        raise RuntimeError(
            f"最终有效账号仍不足: valid={final_valid_count}, min_required={min_valid_accounts}"
        )

    LOGGER.info("维护补充完成: final_valid=%s min_required=%s", final_valid_count, min_valid_accounts)
    return {
        "maintain": maintain_result,
        "after_maintain_scan": after_maintain_scan,
        "refill_upload_count": int(refill_upload_count),
        "refill_upload_result": refill_upload_result,
        "after_refill_scan": after_refill_scan,
        "register_result": register_result,
        "register_upload_result": register_upload_result,
        "final_scan": final_scan,
        "final_valid_count": int(final_valid_count),
    }


def prompt_string(label: str, default: str, *, secret: bool = False) -> str:
    shown_default = default or "空"
    prompt = f"{label}（默认 {shown_default}）: "
    raw = getpass.getpass(prompt) if secret else input(prompt)
    raw = raw.strip()
    return raw or default


def prompt_int(label: str, default: int, *, min_value: int = 0) -> int:
    raw = input(f"{label}（默认 {default}）: ").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except Exception:
        print("输入无效，使用默认值。")
        return default
    if value < min_value:
        print(f"输入过小，使用最小值 {min_value}。")
        return min_value
    return value


def prompt_float(label: str, default: float, *, min_value: float = 0.0, max_value: float | None = None) -> float:
    raw = input(f"{label}（默认 {default:g}）: ").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except Exception:
        print("输入无效，使用默认值。")
        return default
    if value < min_value:
        print(f"输入过小，使用最小值 {min_value:g}。")
        return min_value
    if max_value is not None and value > max_value:
        print(f"输入过大，使用最大值 {max_value:g}。")
        return max_value
    return value


def prompt_yes_no(label: str, default: bool) -> bool:
    default_text = "yes" if default else "no"
    raw = input(f"{label}（默认 {default_text}）[yes/no]: ").strip().lower()
    if not raw:
        return default
    if raw in {"y", "yes"}:
        return True
    if raw in {"n", "no"}:
        return False
    print("输入无效，使用默认值。")
    return default


def prompt_choice(label: str, options: list[str], default: str) -> str:
    raw = input(f"{label}（默认 {default}）[{ '/'.join(options) }]: ").strip().lower()
    if not raw:
        return default
    if raw in options:
        return raw
    print("输入无效，使用默认值。")
    return default


def choose_mode_interactive() -> str:
    print("\n请选择操作:")
    print("1) scan - 检测 401 和限额并导出")
    print("2) maintain - 删除 401、处理限额、恢复账号")
    print("3) upload - 从目录并发上传认证文件")
    print("4) maintain-refill - 维护后自动补充到最小有效账号数")
    print("0) exit")
    while True:
        choice = input("请输入选项编号: ").strip()
        if choice == "1":
            return "scan"
        if choice == "2":
            return "maintain"
        if choice == "3":
            return "upload"
        if choice == "4":
            return "maintain-refill"
        if choice == "0":
            return "exit"
        print("无效选项，请重新输入。")


def prompt_interactive_settings(settings: dict[str, Any]) -> dict[str, Any]:
    settings["timeout"] = prompt_int("请输入请求超时 timeout(秒)", settings["timeout"], min_value=1)
    settings["db_path"] = prompt_string("请输入 SQLite 路径 db_path", settings["db_path"])
    settings["log_file"] = prompt_string("请输入日志文件路径 log_file", settings["log_file"])
    settings["debug"] = prompt_yes_no("是否开启调试模式 debug", settings["debug"])

    if settings["mode"] in {"scan", "maintain", "maintain-refill"}:
        settings["probe_workers"] = prompt_int("请输入探测并发 probe_workers", settings["probe_workers"], min_value=1)
        settings["retries"] = prompt_int("请输入失败重试 retries", settings["retries"], min_value=0)
        settings["target_type"] = prompt_string("请输入 target_type", settings["target_type"])
        settings["provider"] = prompt_string("请输入 provider", settings["provider"])
        settings["quota_disable_threshold"] = prompt_float(
            "请输入限额禁用阈值 quota_disable_threshold(0~1)",
            settings["quota_disable_threshold"],
            min_value=0.0,
            max_value=1.0,
        )
        settings["invalid_output"] = prompt_string("请输入 401 导出路径 invalid_output", settings["invalid_output"])
        settings["quota_output"] = prompt_string("请输入限额导出路径 quota_output", settings["quota_output"])

    if settings["mode"] in {"maintain", "maintain-refill"}:
        settings["action_workers"] = prompt_int("请输入动作并发 action_workers", settings["action_workers"], min_value=1)
        settings["delete_retries"] = prompt_int("请输入删除失败重试 delete_retries", settings["delete_retries"], min_value=0)
        settings["delete_401"] = prompt_yes_no("是否自动删除 401 账号", settings["delete_401"])
        settings["quota_action"] = prompt_choice("限额账号动作 quota_action", ["disable", "delete"], settings["quota_action"])
        settings["auto_reenable"] = prompt_yes_no("是否自动重新启用恢复账号", settings["auto_reenable"])
        settings["reenable_scope"] = prompt_choice(
            "恢复范围 reenable_scope",
            ["signal", "managed"],
            settings["reenable_scope"],
        )
        settings["assume_yes"] = prompt_yes_no("是否跳过危险操作确认", settings["assume_yes"])

    if settings["mode"] in {"upload", "maintain-refill"}:
        settings["upload_dir"] = prompt_string("请输入上传目录 upload_dir", settings["upload_dir"])
        settings["upload_workers"] = prompt_int("请输入上传并发 upload_workers", settings["upload_workers"], min_value=1)
        settings["upload_retries"] = prompt_int("请输入上传重试 upload_retries", settings["upload_retries"], min_value=0)
        settings["upload_method"] = prompt_choice("上传方式 upload_method", ["json", "multipart"], settings["upload_method"])
        settings["upload_recursive"] = prompt_yes_no("是否递归上传子目录 upload_recursive", settings["upload_recursive"])
        settings["upload_force"] = prompt_yes_no("远端同名文件已存在时是否强制上传 upload_force", settings["upload_force"])

    if settings["mode"] == "maintain-refill":
        settings["min_valid_accounts"] = prompt_int("请输入最小有效账号数 min_valid_accounts", settings["min_valid_accounts"], min_value=1)
        settings["refill_strategy"] = prompt_choice("补充策略 refill_strategy", ["to-threshold", "fixed"], settings["refill_strategy"])
        settings["auto_register"] = prompt_yes_no("不足时是否启用外部注册钩子 auto_register", settings["auto_register"])
        settings["register_timeout"] = prompt_int("注册命令超时秒数 register_timeout", settings["register_timeout"], min_value=1)
        settings["register_workdir"] = prompt_string("注册命令工作目录 register_workdir", settings["register_workdir"])
        if settings["auto_register"]:
            settings["register_command"] = prompt_string("注册命令 register_command", settings["register_command"])

    return settings


def ensure_credentials(settings: dict[str, Any], interactive: bool) -> None:
    if not settings["base_url"] and interactive:
        settings["base_url"] = prompt_string("请输入 CPA base_url", "")
    if not settings["token"] and interactive:
        settings["token"] = prompt_string("请输入 CPA 管理 token", "", secret=True)

    settings["base_url"] = settings["base_url"].rstrip("/")

    if not settings["base_url"]:
        raise RuntimeError("缺少 base_url，请在配置文件中提供。")
    if not settings["token"]:
        raise RuntimeError("缺少 token，请在配置文件中提供。")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="基于 auth-files + wham/usage + auth-files 上传的交互式 CPA 账号维护脚本")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH, help="配置文件路径（默认: config.json）")
    parser.add_argument("--mode", choices=["scan", "maintain", "upload", "maintain-refill"], help="运行模式")
    parser.add_argument("--target-type", help="按 files[].type 过滤")
    parser.add_argument("--provider", help="按 provider 过滤")
    parser.add_argument("--probe-workers", type=int, help="api-call 探测并发")
    parser.add_argument("--action-workers", type=int, help="删除/禁用/启用并发")
    parser.add_argument("--timeout", type=int, help="请求超时秒数")
    parser.add_argument("--retries", type=int, help="单账号探测失败重试次数")
    parser.add_argument("--delete-retries", type=int, help="删除动作失败重试次数")
    parser.add_argument("--user-agent", help="wham/usage 探测使用的 User-Agent")
    parser.add_argument("--quota-action", choices=["disable", "delete"], help="限额账号处理动作")
    parser.add_argument("--quota-disable-threshold", type=float, help="剩余额度比例触发禁用阈值（0~1，默认 0 表示仅耗尽）")
    parser.add_argument("--db-path", help="SQLite 状态库路径")
    parser.add_argument("--invalid-output", help="401 导出文件路径")
    parser.add_argument("--quota-output", help="限额导出文件路径")
    parser.add_argument("--log-file", help="日志文件路径")
    parser.add_argument("--debug", action="store_true", help="开启调试模式，在终端打印更详细信息")
    parser.add_argument("--upload-dir", help="upload 模式下认证文件目录")
    parser.add_argument("--upload-workers", type=int, help="upload 模式并发数")
    parser.add_argument("--upload-retries", type=int, help="upload 模式失败重试次数")
    parser.add_argument("--upload-method", choices=["json", "multipart"], help="upload 模式上传方式")
    parser.add_argument("--min-valid-accounts", type=int, help="maintain-refill 模式下最小有效账号阈值")
    parser.add_argument("--refill-strategy", choices=["to-threshold", "fixed"], help="maintain-refill 模式补充策略")
    parser.add_argument("--register-command", help="外部注册命令（仅 maintain-refill + auto_register）")
    parser.add_argument("--register-timeout", type=int, help="外部注册命令超时秒数")
    parser.add_argument("--register-workdir", help="外部注册命令工作目录")
    upload_force_group = parser.add_mutually_exclusive_group()
    upload_force_group.add_argument("--upload-force", dest="upload_force", action="store_true", help="upload 模式强制上传远端同名文件")
    upload_force_group.add_argument("--no-upload-force", dest="upload_force", action="store_false", help="upload 模式不强制上传远端同名文件")

    delete_group = parser.add_mutually_exclusive_group()
    delete_group.add_argument("--delete-401", dest="delete_401", action="store_true", help="维护模式下自动删除 401")
    delete_group.add_argument("--no-delete-401", dest="delete_401", action="store_false", help="维护模式下不删除 401")

    reenable_group = parser.add_mutually_exclusive_group()
    reenable_group.add_argument("--auto-reenable", dest="auto_reenable", action="store_true", help="维护模式下自动启用恢复账号")
    reenable_group.add_argument("--no-auto-reenable", dest="auto_reenable", action="store_false", help="维护模式下不自动启用恢复账号")
    parser.add_argument(
        "--reenable-scope",
        choices=["signal", "managed"],
        help="自动恢复候选范围：signal=按实时信号，managed=仅恢复本工具曾禁用账号",
    )

    register_group = parser.add_mutually_exclusive_group()
    register_group.add_argument("--auto-register", dest="auto_register", action="store_true", help="maintain-refill 模式不足时启用外部注册命令")
    register_group.add_argument("--no-auto-register", dest="auto_register", action="store_false", help="maintain-refill 模式不足时不启用外部注册命令")

    recursive_group = parser.add_mutually_exclusive_group()
    recursive_group.add_argument("--upload-recursive", dest="upload_recursive", action="store_true", help="upload 模式递归扫描子目录")
    recursive_group.add_argument("--no-upload-recursive", dest="upload_recursive", action="store_false", help="upload 模式仅扫描当前目录")

    parser.set_defaults(
        delete_401=None,
        auto_reenable=None,
        auto_register=None,
        debug=None,
        upload_recursive=None,
        upload_force=None,
    )
    parser.add_argument("--yes", action="store_true", help="跳过删除确认")
    return parser.parse_args()


def run_async_or_exit(coro: Any) -> Any:
    try:
        return asyncio.run(coro)
    except KeyboardInterrupt:
        LOGGER.error("已中断。")
        sys.exit(130)
    except Exception as exc:
        LOGGER.error("错误: %s", exc)
        sys.exit(1)


def main() -> int:
    args = parse_args()
    ensure_aiohttp()

    interactive = False
    if args.mode is None:
        if not sys.stdin.isatty():
            print("错误: 未指定 --mode，且当前不是交互终端。", file=sys.stderr)
            return 1
        interactive = True
        mode = choose_mode_interactive()
        if mode == "exit":
            print("已退出。")
            return 0
        args.mode = mode
        args.config = prompt_string("配置文件路径", args.config)

    config_required = "--config" in sys.argv and not interactive
    conf = load_config_json(args.config, required=config_required)
    settings = build_settings(args, conf)

    if interactive:
        settings = prompt_interactive_settings(settings)

    configure_logging(settings["log_file"], settings["debug"])
    LOGGER.info("日志文件: %s", settings["log_file"])
    LOGGER.info("调试模式: %s", "on" if settings["debug"] else "off")

    ensure_credentials(settings, interactive)
    LOGGER.debug(
        "运行参数: mode=%s target_type=%s provider=%s probe_workers=%s action_workers=%s timeout=%s retries=%s delete_retries=%s quota_action=%s quota_disable_threshold=%s delete_401=%s auto_reenable=%s reenable_scope=%s upload_dir=%s upload_workers=%s upload_retries=%s upload_method=%s upload_recursive=%s upload_force=%s min_valid_accounts=%s refill_strategy=%s auto_register=%s register_timeout=%s register_workdir=%s db_path=%s",
        settings["mode"],
        settings["target_type"],
        settings["provider"] or "",
        settings["probe_workers"],
        settings["action_workers"],
        settings["timeout"],
        settings["retries"],
        settings["delete_retries"],
        settings["quota_action"],
        settings["quota_disable_threshold"],
        settings["delete_401"],
        settings["auto_reenable"],
        settings["reenable_scope"],
        settings["upload_dir"],
        settings["upload_workers"],
        settings["upload_retries"],
        settings["upload_method"],
        settings["upload_recursive"],
        settings["upload_force"],
        settings["min_valid_accounts"],
        settings["refill_strategy"],
        settings["auto_register"],
        settings["register_timeout"],
        settings["register_workdir"],
        settings["db_path"],
    )

    conn = connect_db(settings["db_path"])
    try:
        init_db(conn)
        if settings["mode"] == "scan":
            run_async_or_exit(run_scan_async(conn, settings))
            return 0
        if settings["mode"] == "maintain":
            run_async_or_exit(run_maintain_async(conn, settings))
            return 0
        if settings["mode"] == "maintain-refill":
            run_async_or_exit(run_maintain_refill_async(conn, settings))
            return 0
        run_async_or_exit(run_upload_async(conn, settings))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
