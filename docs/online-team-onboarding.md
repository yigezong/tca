# 线上 TCA 团队接入

## 共享服务

- GitHub 仓库保存代码、Claim、协议和集体约定。
- 线上 TCA Controller 接收 Claim、维护状态机并保存审计状态。
- Neon Postgres 保存 Controller 的权威状态。
- A 与 M 通过 `https://tca-ten.vercel.app/mcp` 连接 Controller。

## 每位开发成员（A）

1. Clone GitHub 仓库，并确保能够 pull 和 push。
2. 信任仓库内的 `.codex/config.toml`。
3. 在本机提供 `TCA_API_TOKEN`，不得把令牌提交到 Git。
4. 开始共享范围修改前，创建并 push `tca/claim/<claim-id>`。
5. 从 Controller 获得批准并调用 `start_execution` 后，才能进入工作分支执行。
6. 执行期间汇报进度，完成后调用 `complete_claim`。

## 协调节点（M）

1. 使用独立 clone，不与任何 A 共用工作目录或 worktree。
2. 配置同一个线上 TCA MCP，并拥有 GitHub 仓库读取权限。
3. 每次协调前 pull 最新 `main`，读取协议和 `.tca/agreements/`。
4. 通过 `get_coordination_task` 领取任务。
5. 只提交协调建议，不编写业务代码，也不代替 A 执行 Claim。

## 最短协作链路

```text
A push Claim
    → GitHub Webhook 通知 Controller
    → Controller 保存 Claim 并进入 assessing
    → M 被唤醒并领取协调任务
    → M 提交建议，Controller 决定状态
    → A 读取状态；获批后开始执行
    → A 汇报进度并完成 Claim
```

MCP 负责通信和协议工具，不会自行唤醒 Agent。正式运行还需要一个 M Runner：
它在新 Claim 到达时唤醒 M，或定期让 M 查询待协调队列。

