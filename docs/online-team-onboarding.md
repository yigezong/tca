# TCA 实现、部署与自举开发指南

本文面向第一次接触 TCA 的开发者，解释系统原理、团队配置和自举开发方式。

## 1. 系统全貌

```text
                           ┌──────── Neon Postgres（权威状态）
                           ▼
A1 ── MCP ──┐       ┌──────────────────────────┐
A2 ── MCP ──┼──────►│ Vercel：TCA Server / C   │
M  ── MCP ──┘       │ MCP + Webhook + 状态机   │
                    └────────────▲─────────────┘
                                 │ GitHub Webhook
                    GitHub：代码、Claim、团队约定
```

- **A（开发 Agent）**：发布 Claim，获批后修改代码。
- **M（协调 Agent）**：理解 Claim 之间的语义冲突并提交建议。
- **C（Controller）**：普通确定性程序，不是 Agent；维护状态机、权限和审计。
- **P（人）**：提出目标，处理机器无法裁决的问题。

C、MCP 和 Webhook 运行在 Vercel。M 不在 TCA Server 内；M 和 A 都是外部 Agent。

## 2. 实现原理

### 2.1 GitHub 发布意图

A 修改共享代码前，创建 `tca/claim/<claim-id>` 分支，并提交 `.tca/claim.yaml`。Claim 描述目标、基线提交、工作分支、预计路径、排除路径和依赖。

Push 后，GitHub Webhook 通知 C。C 验证 HMAC，从 GitHub 读取 Claim，再写入 Neon。Webhook 只负责通知；Neon 中的 C 状态才是运行时权威。

### 2.2 C 硬控状态机

```text
received → assessing → approved / waiting / needs_human
                          │
                          ▼
                      executing → completed
```

`assessing` 时 A 只能等待；`approved` 后才能调用 `start_execution`；`executing` 时才能汇报或完成。M 只能提交建议，不能直接进入执行或完成状态。因此 Agent 即使遗漏 Prompt，也无法通过 C 做非法转换。

### 2.3 M 做语义协调

M 调用 `get_coordination_task` 领取任务，读取活跃 Claim、依赖和 `.tca/agreements/`，再通过 `submit_coordination_decision` 建议批准、等待、阻塞或转交人类。C 校验建议并转换状态。

### 2.4 MCP 的边界

MCP 向 A、M 暴露 C 的权威状态和工具，并在初始化时下发协议指令。它不保存代码、不替代 GitHub、不替 M 判断语义，也**不会自动启动或唤醒 Agent**。

当前仍有一处人工环节：

```text
C 收到 Claim → [人工通知 M] → M 领取任务
```

下一阶段需要 `M Runner`：轮询 C，或调用具体 Agent 平台的任务接口唤醒 M。不同商用 Agent 的唤醒方式不同，所以 Runner 是可替换适配层。

## 3. 完整工作流

```text
1. P 给 A 目标
2. A 创建并 push Claim
3. GitHub Webhook 通知 C
4. C 保存 Claim，进入 assessing
5. M 被唤醒并 pull 最新 main
6. M 从 C 领取任务并提交建议
7. C 批准、等待或转交人类
8. A 读取结果；获批后调用 start_execution
9. A 在工作分支修改代码并汇报进度
10. A push 工作分支并调用 complete_claim
11. C 完成 Claim，重新评估被阻塞任务
```

GitHub 保存代码和团队文档；Neon 保存运行时状态，两者不能互相替代。

## 4. GitHub、Vercel 与 Neon 配置

### GitHub

所有成员都要能访问仓库。私有仓库给 Vercel 使用的 fine-grained token 至少需要 Metadata Read 和 Contents Read。

在 **Settings → Webhooks** 添加：

```text
Payload URL: https://<vercel-domain>/webhooks/github
Content type: application/json
Secret: 与 GITHUB_WEBHOOK_SECRET 相同
Events: Just the push event
Active: 开启
```

Recent Deliveries 返回 `202` 即连接成功。

### Vercel 与 Neon

1. 在 Vercel 导入 GitHub 仓库。
2. 添加 Neon Postgres，连接 Production，确认生成 `DATABASE_URL`。
3. 配置 Production 环境变量：

| 变量 | 用途 |
|---|---|
| `DATABASE_URL` | Neon 连接串 |
| `TCA_API_TOKEN` | MCP/API Bearer Token |
| `TCA_COORDINATION_MODE=external` | 使用外部 M |
| `GITHUB_WEBHOOK_SECRET` | 验证 Webhook HMAC |
| `GITHUB_TOKEN` | 读取私有仓库 Claim |
| `TCA_CLAIM_BRANCH_PREFIX=tca/claim/` | Claim 分支前缀 |
| `TCA_PUBLISH_GIT_DECISIONS=0` | A 从 MCP 读取 Decision |

Secret 可用 `openssl rand -hex 32` 生成。环境变量变更后必须 Redeploy。

```bash
curl https://<vercel-domain>/health
# {"status":"ok","store":"postgres"}
```

## 5. A 的环境配置

每位成员使用自己的 clone，并确保 GitHub pull/push 正常。仓库内保存共享 MCP 地址：

```toml
[mcp_servers.tca]
url = "https://<vercel-domain>/mcp"
bearer_token_env_var = "TCA_API_TOKEN"
```

真实 Token 只在个人环境提供，禁止提交。修改 MCP 配置后要重启 Codex；已有线程不会热加载。A 还要读取 `AGENTS.md` 和 `.tca/protocol.md`，并在 C 批准前停止共享范围修改。

## 6. M 的环境配置

M 使用独立 clone，不能与 A 共用目录或 worktree。M 需要：

- GitHub 读取权限，能执行 `git pull origin main`；
- 与 A 相同的 MCP 地址和 Token；
- 固定角色指令：只协调，不编写业务代码；
- 每次协调前 pull 最新协议与集体约定；
- 从 C 领取权威任务，不相信通知正文或本地记忆。

当前由人唤醒 M；以后由 M Runner 完成。

## 7. 本地开发与测试

```bash
uv sync
uv run pytest
uv run tca-app
```

未设置 `DATABASE_URL` 时使用 SQLite；设置后使用 Postgres。部署改动还要验证：

- `/health` 公共健康检查；
- `/webhooks/github` 的 HMAC 和 Claim 解析；
- `/mcp` 的 Bearer 鉴权、initialize 和 tools；
- Neon 状态跨请求持久化。

## 8. 自举：用 TCA 开发 TCA

自举不是 TCA 自动编写自己，而是 TCA 的开发也遵守 TCA 协议。

首次线上验证过程：

1. A1 创建并 push 文档 Claim；
2. GitHub 自动通知 C，C 写入 Neon并进入 `assessing`；
3. 人工通知 M 查看队列；
4. M 领取任务并建议 `approved`；
5. C 批准，A1 调用 `start_execution`；
6. A1 完成文档、push 工作分支并调用 `complete_claim`；
7. C 记录 `completed`，revision 6。

这证明 Webhook、C、Neon、MCP、M 协调和 A 执行形成了核心闭环；也证明 M 自动唤醒尚未实现。完成 M Runner 后，才是自动自举。

后续迭代遵循：

```text
旧版 TCA 接收 Claim → M 协调 → 开发新版 → 测试真实 Claim → 新版接管下一轮
```

## 9. 安全边界

- `/health` 公开；Webhook 使用 HMAC；`/mcp` 和 `/api/*` 使用 Bearer Token。
- V1 的受信任团队暂时由 A、M 共享一个 Token。
- Token、GitHub PAT 和数据库连接串不得提交 Git。
- Secret 泄露后，应同时轮换服务端和所有客户端。

详细状态规则见 [`.tca/protocol.md`](../.tca/protocol.md)。
