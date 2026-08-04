# TCA 指南

TCA 是一个 Git-first 的多 Agent 协作控制服务。它的核心目标是让多个 Agent 在同一个 repo 中工作时，先发布意图，再经过共享状态机协调，最后才进入代码修改。

本仓库是 TCA Server 源码仓库，不是被测 repo。TCA 本体不保存 `.tca/` 或 `.codex/` 活配置。

## 当前架构

```text
A / M Agent ── MCP ──┐
                     ▼
Git provider ─ webhook ─► TCA Server / C ─► 状态数据库
```

组件职责：

- **C / Controller**：权威状态机，校验 Claim、维护协调队列、返回 `allowed_actions`。
- **A / 开发 Agent**：提出 Claim，获批后执行修改，并通过 MCP 开始和完成任务。
- **M / 协调 Agent**：从 MCP 领取待协调 Claim，判断冲突、依赖和约束，并提交建议。
- **Git provider**：保存代码、Claim 分支和 `.tca/claim.yaml`。
- **状态数据库**：保存 Claim 状态和协调任务；本地用 SQLite，部署用 Postgres。

## Claim 流程

```text
1. A 创建 tca/claim/<claim-id> 分支
2. A 在分支中提交 .tca/claim.yaml
3. Git provider webhook 通知 TCA Server
4. C 读取 Claim 并写入状态数据库
5. M 通过 MCP 领取协调任务并提交建议
6. A 通过 MCP 等待决策
7. C 返回 state、decision 和 allowed_actions
8. A 只有在 allowed_actions 包含 start_execution 时才开始修改
9. A 完成后调用 complete_claim
```

C 是唯一权威状态源。A 和 M 都不能直接改数据库，也不能自行推断状态。

## Server 部署配置

TCA Server 部署时只需要当前服务本身的配置：

```bash
TCA_API_TOKEN=...
DATABASE_URL=...
TCA_ALLOWED_HOSTS=...
```

| 变量 | 作用 |
|---|---|
| `TCA_API_TOKEN` | 保护 `/mcp` 和 `/api` 的 Bearer token |
| `DATABASE_URL` | 线上 Postgres 连接串 |
| `TCA_ALLOWED_HOSTS` | MCP 允许的主机名，不带 `https://` |

示例：

```bash
TCA_ALLOWED_HOSTS=tca.example.com
```

`/health` 不需要 token，用于部署检查。`/mcp` 和 `/api/*` 需要：

```text
Authorization: Bearer <TCA_API_TOKEN>
```

## GitHub Webhook

`fringsoo/tca` 是 TCA Server 源码 repo，不给自己配置 `/webhooks/github`。

`/webhooks/github` 只给独立被测 repo 使用。接入被测 repo 时，需要在 Server 环境里增加 GitHub adapter 配置，并在被测 repo 的 GitHub Settings -> Webhooks 中配置：

```text
Payload URL: https://<tca-server-host>/webhooks/github
Content type: application/json
Events: Push events
```

这些配置不属于当前 TCA Server 最小部署项。

## 被测 Repo 模板

被测 repo 需要提交 TCA 协议文件。本仓库提供模板：

```text
templates/test-repo/AGENTS.md
templates/test-repo/.tca/protocol.md
templates/test-repo/.tca/project.yaml
templates/test-repo/.tca/claim.example.yaml
```

复制到被测 repo 后，替换 `.tca/project.yaml` 里的 `project_id` 和 `mcp.url`。

## 文件地图

根目录：

| 文件 | 作用 |
|---|---|
| `README.md` | 项目入口 |
| `AGENTS.md` | 本仓库的 Agent 工作规则 |
| `.env.example` | Vercel 环境变量模板 |
| `app.py` | Vercel ASGI 入口 |
| `vercel.json` | Vercel 函数配置 |
| `pyproject.toml` | Python 包和依赖配置 |
| `uv.lock` | 依赖锁文件 |

代码：

| 文件 | 作用 |
|---|---|
| `src/tca/app.py` | Starlette HTTP 应用，挂载 `/health`、`/api`、`/mcp`、`/webhooks/github` |
| `src/tca/controller.py` | Claim 状态机 |
| `src/tca/domain.py` | 领域模型 |
| `src/tca/rules.py` | 基础规则检查 |
| `src/tca/store.py` | SQLite store 和 store 构造入口 |
| `src/tca/postgres_store.py` | Postgres store |
| `src/tca/github.py` | GitHub webhook 和 Claim 文件读取 |
| `src/tca/mcp_server.py` | MCP tools |

测试：

| 文件 | 作用 |
|---|---|
| `tests/test_controller.py` | 状态机测试 |
| `tests/test_github.py` | GitHub adapter 测试 |
| `tests/test_app.py` | HTTP 鉴权测试 |
