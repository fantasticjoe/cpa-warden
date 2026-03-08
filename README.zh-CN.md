# cpa-warden

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Release](https://img.shields.io/github/v/release/fantasticjoe/cpa-warden)](https://github.com/fantasticjoe/cpa-warden/releases/latest)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)
![uv](https://img.shields.io/badge/deps-uv-6f42c1)

[English](README.md)

`cpa-warden` 是一个面向本地运维场景的交互式 [CLIProxyAPI（CPA）](https://github.com/router-for-me/CLIProxyAPI) 认证文件扫描、上传与账号维护工具，适用于特定 CPA 管理环境。

它当前依赖三类管理接口：`GET /v0/management/auth-files` 用于拉取认证文件清单，`POST /v0/management/api-call` 用于请求 `https://chatgpt.com/backend-api/wham/usage` 并完成用量探测，`POST /v0/management/auth-files` 用于上传认证文件。

## 它能做什么

从使用者视角看，脚本会：

- 拉取当前认证文件清单
- 把本地状态写入 SQLite
- 并发探测 usage
- 在 upload 模式下并发上传本地认证 JSON
- 导出当前的 `401` 和限额账号结果
- 在维护模式下按配置执行删除、禁用或恢复启用
- 可在维护后自动补充至最小有效账号阈值

## 核心能力

- 在 TTY 中未提供 `--mode` 时默认进入交互模式
- 支持可重复执行的非交互 `scan`、`maintain`、`upload`、`maintain-refill` 流程
- 敏感信息通过外部 JSON 配置提供，例如 `base_url` 和 `token`
- 通过 CLIProxyAPI `api-call` 接口并发探测 usage
- 并发上传认证文件，并通过本地 SQLite 去重防止重复上传
- 当补充后仍不足阈值时，可选调用外部注册命令钩子
- 使用本地 SQLite 持久化多轮状态
- 导出失效账号和限额账号 JSON
- 生产模式终端输出简短，在 TTY 下可显示 Rich 进度
- 每次运行都会写入完整的 debug 级别日志文件

## 安全边界与适用范围

`cpa-warden` 面向特定 CPA 管理环境，不是通用账号管理平台。它已经可以用于实际本地运维，但当前仍属于偏早期的开源项目。更准确的定位是：给熟悉 CPA 环境的操作人员使用的维护工具，而不是适配任意认证系统的通用方案。

维护模式可能删除或禁用远端账号。upload 模式会向远端创建认证文件，且可通过 `--upload-force` 强制同名上传。`maintain-refill` 开启后还可能执行外部注册命令。执行前请确认配置是否符合预期，尤其是在使用 `--quota-action delete`、`--upload-force`、`--auto-register` 或 `--yes` 时。

## 环境要求

- Python 3.11+
- [uv](https://docs.astral.sh/uv/)

## 安装

```bash
uv sync
```

## 配置

先复制示例配置：

```bash
cp config.example.json config.json
```

至少需要填写：

- `base_url`
- `token`

`config.json` 已被 git 忽略，不应提交到仓库。

示例配置：

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

布尔型配置会做严格解析。支持写法：`true/false`、`1/0`、`yes/no`、`on/off`（不区分大小写）；非法值会在启动时直接报错。

重要配置项说明：

- `base_url`：CLIProxyAPI 管理接口基础地址
- `token`：CLIProxyAPI 管理 token
- `target_type`：按 `files[].type` 过滤记录
- `provider`：按 `provider` 字段过滤记录
- `probe_workers`：usage 探测并发数
- `action_workers`：删除 / 禁用 / 启用动作并发数
- `timeout`：请求超时时间，单位秒
- `retries`：探测“可重试失败”（超时、网络异常、`429`、`5xx`）时的重试次数
- `delete_retries`：删除失败重试次数（网络异常、`429`、`5xx` 等）
- `quota_action`：限额账号的处理动作，只能是 `disable` 或 `delete`
- `quota_disable_threshold`：按剩余额度比例自动禁用阈值（`0~1`）；`0` 保持旧行为（仅额度耗尽才禁用）
- `delete_401`：维护模式下是否删除 `401` 账号
- `auto_reenable`：是否自动恢复启用已恢复账号
- `reenable_scope`：自动恢复范围，`signal` 或 `managed`（默认：`signal`）
- `upload_dir`：`upload` 模式读取 `.json` 文件的目录
- `upload_workers`：`upload` 模式并发 worker 数
- `upload_retries`：上传失败重试次数
- `upload_method`：上传请求类型，`json` 或 `multipart`
- `upload_recursive`：`upload` 模式是否递归扫描子目录
- `upload_force`：远端已存在同名文件时是否继续上传
- `min_valid_accounts`：`maintain-refill` 模式下的最小有效账号阈值
- `refill_strategy`：`maintain-refill` 补充策略，`to-threshold` 或 `fixed`
- `auto_register`：补充后仍不足时是否调用外部注册命令
- `register_command`：外部注册命令模板（注册逻辑不在本仓库）
- `register_timeout`：外部注册命令超时秒数
- `register_workdir`：外部注册命令工作目录
- `db_path`：本地 SQLite 状态库路径
- `invalid_output`：`401` 账号 JSON 导出路径
- `quota_output`：限额账号 JSON 导出路径
- `log_file`：运行日志路径
- `debug`：是否在终端输出更详细日志
- `user_agent`：探测 `wham/usage` 时使用的 User-Agent

## 使用方式

交互式运行：

```bash
uv run python cpa_warden.py
```

非交互运行示例：

```bash
uv run python cpa_warden.py --mode scan
uv run python cpa_warden.py --mode scan --debug
uv run python cpa_warden.py --mode scan --target-type codex --provider openai
uv run python cpa_warden.py --mode maintain
uv run python cpa_warden.py --mode maintain --no-delete-401 --no-auto-reenable
uv run python cpa_warden.py --mode maintain --quota-action delete
uv run python cpa_warden.py --mode maintain --quota-disable-threshold 0.1
uv run python cpa_warden.py --mode maintain --reenable-scope managed
uv run python cpa_warden.py --mode maintain --quota-action delete --yes
uv run python cpa_warden.py --mode upload --upload-dir ./auth_files
uv run python cpa_warden.py --mode upload --upload-dir ./auth_files --upload-recursive --upload-workers 50
uv run python cpa_warden.py --mode upload --upload-dir ./auth_files --upload-method multipart --upload-force
uv run python cpa_warden.py --mode maintain-refill --min-valid-accounts 200 --upload-dir ./auth_files
uv run python cpa_warden.py --mode maintain-refill --min-valid-accounts 200 --refill-strategy fixed --upload-dir ./auth_files
uv run python cpa_warden.py --mode maintain-refill --min-valid-accounts 200 --upload-dir ./auth_files --auto-register --register-command 'python /opt/register-machine/register.py'
```

支持的 CLI 参数：

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

## 运行模式

### `scan`

`scan` 只会读取账号清单、探测 usage、更新本地 SQLite 状态并导出当前结果，不会修改远端账号状态。

### `maintain`

`maintain` 一定会先执行一次完整的 `scan`，然后再按配置执行后续动作：

- 删除 `401` 账号
- 禁用或删除限额账号
- 重新启用已恢复账号

说明：默认 `reenable_scope=signal` 时，`auto_reenable` 也可能重新启用“人工禁用但当前满足恢复条件”的账号。若只想恢复本工具曾标记为 `quota_disabled` 的账号，请使用 `reenable_scope=managed`。

删除动作默认需要确认；提供 `--yes` 后会跳过危险操作确认。

### `upload`

`upload` 会从 `upload_dir` 读取 `.json` 文件，校验内容后并发上传到 `/v0/management/auth-files`。

上传状态会写入 SQLite，并以 `{base_url, file_name, content_sha256}` 作为唯一键保证“同一文件只上传一次”，避免并发导致重复上传。

如果存在任意文件校验失败或上传失败，命令会以非零状态码退出。

### `maintain-refill`

`maintain-refill` 会先执行 `maintain`，然后按严格规则计算有效账号（`disabled=0`、非 401、非限额、且无探测错误）。  
若有效账号数低于 `min_valid_accounts`，则从 `upload_dir` 补充上传，补充规则由 `refill_strategy` 决定：

- `to-threshold`：仅补齐缺口（`min_valid_accounts - 当前有效数`）
- `fixed`：每次触发都固定上传 `min_valid_accounts` 个

若补充后仍低于阈值且 `auto_register=true`，将调用外部 `register_command`（不在本仓库实现）：

- 本工具自动追加参数：`--count <N> --output-dir <upload_dir>`
- 注入环境变量：`CPA_REGISTER_COUNT`、`CPA_REGISTER_OUTPUT_DIR`

注册逻辑保持外置，本项目仅提供调用钩子。

## 过滤与判定规则

过滤规则：

- `target_type` 基于 `files[].type`
- `provider` 是对 `provider` 字段做大小写不敏感的精确匹配，不是 provider 类型枚举

判定规则：

- `401`：`unavailable == true` 或 `api-call.status_code == 401`
- `quota limited`：`api-call.status_code == 200`，且满足其一：1）“有效限额信号”已耗尽（`limit_reached == true`）；2）剩余额度比例 `<= quota_disable_threshold`。`plan_type=pro` 时优先使用 `additional_rate_limits` 的 Spark（`metered_feature=codex_bengalfox`，回退规则为 `limit_name` 包含 `Spark`）；仅当 Spark 的 `limit_reached` 可用时才使用 Spark，否则回退到顶层 `rate_limit`
- `recovered`：账号当前为禁用状态，且当前同一“有效限额信号”满足 `allowed == true` 且 `limit_reached == false`

探测重试规则：

- `429` 和 `5xx`：按 `retries` 次数进行退避重试
- 其他 `4xx`：快速失败，不重试

## 输出文件

默认输出物：

- `cpa_warden_state.sqlite3`：本地 SQLite 状态数据库
- `cpa_warden_401_accounts.json`：失效 `401` 账号导出
- `cpa_warden_quota_accounts.json`：限额账号导出
- `cpa_warden.log`：运行日志

## 日志与调试行为

- 生产模式的终端输出尽量简短
- 如果当前会话是 TTY，生产模式会优先显示 Rich 进度
- `--debug` 或 `debug: true` 会让终端输出更详细
- 日志文件始终保留完整的 debug 级别信息

## 项目结构

- `cpa_warden.py`：主入口脚本
- `config.example.json`：示例配置
- `pyproject.toml`：项目元数据与依赖
- `.github/workflows/ci.yml`：基础 CI 检查

## 贡献说明

见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 更新记录

见 [CHANGELOG.md](CHANGELOG.md)。

## 安全说明

负责任披露流程见 [SECURITY.md](SECURITY.md)。

## 许可证

MIT，详见 [LICENSE](LICENSE)。
