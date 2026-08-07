# Team Code Agent

Team Code Agent（TCA）是一个 Git-first 的多 Agent 协作控制服务。它只做一件事：在多个 Agent 修改同一个 repo 前，把“谁准备改什么、是否冲突、什么时候可以开始、什么时候完成”变成一个共享的权威状态。

本仓库是 TCA Server 源码仓库，不是被 TCA 管理的业务 repo。本仓库自身不使用 TCA 自举，因此根目录不保存 `.tca/` 或 `.codex/` 活配置。被测 repo 的接入模板在 `templates/test-repo/`。

## 核心角色

```text
GitHub Claim 分支 ── webhook ──► TCA Server / C ──► Postgres
        ▲                              ▲
        │                              │
        A 本机 / Codex ───── MCP ──────┘
        M 本机 / Codex ───── MCP ──────┘
```

- **C / TCA Server**：轻量云服务，只维护状态机、队列和鉴权。
- **A / 开发 Agent**：在被测 repo 工作区提交 Claim，获批后改代码。
- **M / 协调 Agent**：本机运行的外部 Agent，通过 MCP 领取协调任务并提交决策。
- **Git provider**：当前支持 GitHub webhook，Claim 分支固定为 `tca/claim/<agent-id>/<claim-id>`。

## 本地开发

```bash
uv sync
uv run pytest
uv run tca-app
```

本地未设置 `DATABASE_URL` 时使用 SQLite `tca.db`。线上部署时设置 `DATABASE_URL` 后使用 Postgres。

## 部署 TCA Server

Vercel 部署本仓库即可。`fringsoo/tca` 自己不需要配置 `/webhooks/github`；那个 webhook 是给被测 repo 用的。

最小环境变量：

```bash
TCA_API_TOKEN=replace-with-random-token
DATABASE_URL=replace-with-postgres-url
TCA_ALLOWED_HOSTS=tca.example.com
```

GitHub 被测 repo 需要 webhook 时，再加：

```bash
GITHUB_WEBHOOK_SECRET=replace-with-webhook-secret
GITHUB_TOKEN=replace-with-github-token
```

部署后验证：

```bash
curl https://<tca-server-host>/health
```

## 最小使用

被测 repo 复制 `templates/test-repo/` 后，改 `.tca/project.yaml`：

```yaml
version: 1
project_id: owner/repo
mcp:
  url: https://tca.example.com/mcp
  token_env: TCA_API_TOKEN
```

A 在被测 repo 工作区提供：

```bash
TCA_API_TOKEN=...
TCA_AGENT_ID=agent-a1
```

M 可以在任意本机目录运行，不需要被测 repo checkout：

```bash
TCA_MCP_URL=https://tca.example.com/mcp
TCA_PROJECT_ID=owner/repo
TCA_API_TOKEN=...
```

真实用户不需要 checkout 本仓库。A/M 使用 Codex、Claude、Cursor 等客户端的原生 MCP 配置，直接连接 `https://tca.example.com/mcp`。本仓库自带的 `tca-client` 只是维护者本地开发和 smoke test 的辅助命令。

M 不需要 TCA Server 源码；但如果 M 要做语义判断，它需要被测 repo 的只读上下文，例如本地只读 checkout 或 Git provider 只读访问。只靠 TCA 返回的 Claim 元数据，只适合做路径级和依赖级判断。

完整原理和操作手册见 [docs/tca-guide.md](docs/tca-guide.md)。
