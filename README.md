# Team Code Agent

Team Code Agent（TCA）是一个 Git-first 的多 Agent 协作控制服务。它提供一个共享的 TCA Server，让开发 Agent 在修改共享代码前先发布 Claim、等待协调决策，再执行代码修改。

本仓库是 **TCA Server 源码仓库**，不是被 TCA 管理的被测 repo。根目录不保存 `.tca/` 或 `.codex/` 活配置。

## 架构

```text
A / M Agent ── MCP ───────┐
                          ▼
Git provider ─ webhook ─► TCA Server / C ─► 状态数据库
```

- C 是确定性 Controller，维护 Claim 状态机和允许动作。
- A 是开发 Agent，通过 MCP 查询状态、开始执行和完成任务。
- M 是外部协调 Agent，通过 MCP 领取 Claim 并提交协调建议。
- Git provider 保存 Claim 分支和 Claim 文件；当前代码包含 GitHub adapter。
- 非 GitHub 场景可以通过 MCP `submit_claim` 直接提交 Claim。
- 状态数据库本地可用 SQLite，部署使用 Postgres。

## 本地开发

```bash
uv sync
uv run python -m pytest
uv run tca-app
```

## Vercel 部署

Vercel 只需要把本仓库部署成 TCA Server。`fringsoo/tca` 自己不配置指向 `/webhooks/github` 的 webhook。

必需环境变量：

```bash
TCA_API_TOKEN=...
DATABASE_URL=...
TCA_ALLOWED_HOSTS=...
```

- `TCA_API_TOKEN`：A/M 调用 `/mcp` 和 `/api` 的 Bearer token。
- `DATABASE_URL`：线上 Postgres 连接串。
- `TCA_ALLOWED_HOSTS`：`/mcp` 的 Host header 白名单，填 TCA Server 域名，不带 `https://`，例如 `tca.example.com`。

部署后用 `/health` 验证服务：

```bash
curl https://<tca-server-host>/health
```

## 文档

- [docs/tca-guide.md](docs/tca-guide.md)：当前架构、配置和文件地图。
- [templates/test-repo](templates/test-repo)：被测 repo 的最小接入模板。
