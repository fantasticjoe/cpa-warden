# cpa-warden

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Release](https://img.shields.io/github/v/release/fantasticjoe/cpa-warden)](https://github.com/fantasticjoe/cpa-warden/releases/latest)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)
![uv](https://img.shields.io/badge/deps-uv-6f42c1)

[English](README.md)

`cpa-warden` 是一个面向本地运维场景的交互式 [CLIProxyAPI（CPA）](https://github.com/router-for-me/CLIProxyAPI) 认证文件扫描与账号维护工具，适用于特定 CPA 管理环境。

它当前依赖两类管理接口：`GET /v0/management/auth-files` 用于拉取认证文件清单，`POST /v0/management/api-call` 用于请求 `https://chatgpt.com/backend-api/wham/usage` 并完成用量探测。

## 它能做什么

从使用者视角看，脚本会：

- 拉取当前认证文件清单
- 把本地状态写入 SQLite
- 并发探测 usage
- 导出当前的 `401` 和限额账号结果
- 在维护模式下按配置执行删除、禁用或恢复启用

## 核心能力

- 在 TTY 中未提供 `--mode` 时默认进入交互模式
- 支持可重复执行的非交互 `scan` 与 `maintain` 流程
- 敏感信息通过外部 JSON 配置提供，例如 `base_url` 和 `token`
- 通过 CLIProxyAPI `api-call` 接口并发探测 usage
- 使用本地 SQLite 持久化多轮状态
- 导出失效账号和限额账号 JSON
- 生产模式终端输出简短，在 TTY 下可显示 Rich 进度
- 每次运行都会写入完整的 debug 级别日志文件

## 安全边界与适用范围

`cpa-warden` 面向特定 CPA 管理环境，不是通用账号管理平台。它已经可以用于实际本地运维，但当前仍属于偏早期的开源项目。更准确的定位是：给熟悉 CPA 环境的操作人员使用的维护工具，而不是适配任意认证系统的通用方案。

维护模式可能删除或禁用远端账号。执行前请确认配置是否符合预期，尤其是在使用 `--quota-action delete` 或 `--yes` 时。

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

重要配置项说明：

- `base_url`：CLIProxyAPI 管理接口基础地址
- `token`：CLIProxyAPI 管理 token
- `target_type`：按 `files[].type` 过滤记录
- `provider`：按 `provider` 字段过滤记录
- `probe_workers`：usage 探测并发数
- `action_workers`：删除 / 禁用 / 启用动作并发数
- `timeout`：请求超时时间，单位秒
- `retries`：探测失败时的重试次数
- `quota_action`：限额账号的处理动作，只能是 `disable` 或 `delete`
- `delete_401`：维护模式下是否删除 `401` 账号
- `auto_reenable`：是否自动恢复启用已恢复账号
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
uv run python cpa_warden.py --mode maintain --quota-action delete --yes
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

## 运行模式

### `scan`

`scan` 只会读取账号清单、探测 usage、更新本地 SQLite 状态并导出当前结果，不会修改远端账号状态。

### `maintain`

`maintain` 一定会先执行一次完整的 `scan`，然后再按配置执行后续动作：

- 删除 `401` 账号
- 禁用或删除限额账号
- 重新启用已恢复账号

删除动作默认需要确认；提供 `--yes` 后会跳过危险操作确认。

## 过滤与判定规则

过滤规则：

- `target_type` 基于 `files[].type`
- `provider` 是对 `provider` 字段做大小写不敏感的精确匹配，不是 provider 类型枚举

判定规则：

- `401`：`unavailable == true` 或 `api-call.status_code == 401`
- `quota limited`：`api-call.status_code == 200` 且 `body.rate_limit.limit_reached == true`
- `recovered`：此前被标记为 `quota_disabled`，且当前 `allowed == true` 且 `limit_reached == false`

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
- `clean_codex_accounts.py`：旧命令名的兼容包装脚本
- `config.example.json`：示例配置
- `pyproject.toml`：项目元数据与依赖
- `.github/workflows/ci.yml`：基础 CI 检查

## 兼容性说明

`clean_codex_accounts.py` 只是对 `cpa_warden.main()` 的兼容包装。新的使用方式应优先以 `cpa_warden.py` 为主入口。

## 贡献说明

见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 更新记录

见 [CHANGELOG.md](CHANGELOG.md)。

## 安全说明

负责任披露流程见 [SECURITY.md](SECURITY.md)。

## 许可证

MIT，详见 [LICENSE](LICENSE)。
