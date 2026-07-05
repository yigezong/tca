# Team Code Agent

Team Code Agent（TCA）是一个 Git-first 的多 Agent 协作控制服务：GitHub 发布 Claim，Vercel 承载 MCP/Webhook/C，Neon Postgres 保存权威状态。

## 目标结构

```text
A1（本机 proj_tca） ──MCP──┐
M （本机 proj_tca_m）──MCP──┼── Vercel TCA / C ── Neon Postgres
A2（同事的 clone）   ──MCP──┘          ▲
                                       │ GitHub Webhook
GitHub fringsoo/tca ───────────────────┘
```

TCA 不包含 M。A 与 M 都是外部 Agent，通过 C 获取状态和允许操作。

## 当前能力

- GitHub Claim 分支 Webhook 与 HMAC 校验；
- C 的确定性状态机、权限和审计；
- SQLite 本地开发后端；
- Neon/Postgres 公网持久化后端；
- A 的状态、等待、执行、心跳、进度和完成 MCP tools；
- M 的领取协调任务和提交建议 MCP tools；
- Vercel ASGI/Fluid Compute 入口；
- MCP 和 Claim API 的 Bearer Token 鉴权；
- 可选的本地 bare Git hook 接入，默认关闭。

## 本地开发

未设置 `DATABASE_URL` 时自动使用 SQLite：

```bash
uv sync
uv run pytest
uv run tca-app
```

设置 `DATABASE_URL` 时自动使用 Postgres。

## Vercel + Neon 部署

1. 在 Vercel 导入 GitHub 仓库 `fringsoo/tca`。
2. 在项目 Storage/Marketplace 中添加 Neon Postgres，确认产生 `DATABASE_URL`。
3. 在 Vercel Production 环境配置：

```text
TCA_API_TOKEN=<openssl rand -hex 32>
TCA_COORDINATION_MODE=external
GITHUB_WEBHOOK_SECRET=<openssl rand -hex 32>
GITHUB_TOKEN=<fine-grained GitHub token>
TCA_CLAIM_BRANCH_PREFIX=tca/claim/
TCA_PUBLISH_GIT_DECISIONS=0
```

`GITHUB_TOKEN` 对私有仓库至少需要 Metadata read 和 Contents read。V1 默认不向 Claim 分支写回 Decision，A 通过 MCP 获取权威状态。

4. 部署后验证：

```bash
curl https://<vercel-domain>/health
```

5. GitHub 仓库 Settings → Webhooks：

```text
Payload URL: https://<vercel-domain>/webhooks/github
Content type: application/json
Secret: 与 GITHUB_WEBHOOK_SECRET 相同
Event: Push events
```

6. 将实际 MCP URL 写入 `.tca/project.yaml` 和 `.codex/config.toml`，团队成员 pull 后即可连接；Token 只通过个人环境变量 `TCA_API_TOKEN` 提供。

## 安全边界

`/health` 和签名 Webhook 可公开访问；`/mcp` 与 `/api/claims/*` 需要 Bearer Token。当前 V1 面向受信任小团队，A/M 暂时共享服务 Token；角色级 Token 是后续增强项。

完整协议见 [`.tca/protocol.md`](.tca/protocol.md)，产品模型见 [`docs/ideas/project-idea.md`](docs/ideas/project-idea.md)。
