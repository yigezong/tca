# Team Code Agent

Team Code Agent（TCA）是一个 Git-first 的多 Agent 协作控制服务。它只做一件事：在多个 Agent 修改同一个 repo 前，把“谁准备改什么、是否冲突、什么时候可以开始、什么时候完成”变成一个共享的权威状态。

本仓库是 TCA Server 源码仓库，不是被 TCA 管理的业务 repo。本仓库自身不使用 TCA 自举，因此根目录不保存 `.tca/` 或 `.codex/` 活配置。被测 repo 的接入模板在 `templates/test-repo/`。

## 核心角色

```text
GitHub Claim / 默认分支 ─ webhook ─► TCA Server / C ─► Postgres
        ▲                              ▲
        │                              │
        A 本机 / Codex ───── MCP ──────┘
        M 本机 / Codex ───── MCP ──────┘
```

- **C / TCA Server**：轻量云服务，只维护状态机、队列和鉴权。
- **A / 开发 Agent**：在被测 repo 工作区提交 Claim，获批后改代码。
- **M / 协调 Agent**：本机运行的外部 Agent，通过 MCP 领取协调任务并提交决策。
- **集成者**：按团队正常 Git 流程把 completed 工作分支合入目标分支。
- **Git provider**：当前支持 GitHub webhook；Claim 分支用于申报，默认分支 push 用于自动确认集成。

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

## 团队怎么接入

TCA Server 管理者只部署一次服务，并生成一个 `TCA_API_TOKEN`。当前极简版本把它作为团队共享的服务访问密钥：成员只领取和保存，不各自生成。`DATABASE_URL`、`GITHUB_TOKEN`、`GITHUB_WEBHOOK_SECRET` 等服务端变量也只由管理者配置。

一个 Server 可以用 `project_id` 同时保存多个项目的 Claim，但当前只有 Server 级共享 token 和 webhook secret，没有项目级权限隔离。因此一个实例只适合同一信任范围内的团队和项目；要向互不信任的团队提供公共服务，还需先实现项目级凭证和授权。

项目发起者把 `templates/test-repo/` 复制到被测 repo，修改并提交 `.tca/project.yaml`：

```yaml
version: 1
project_id: owner/repo
mcp:
  url: https://tca.example.com/mcp
  token_env: TCA_API_TOKEN
```

项目发起者还要在被测 GitHub repo 配置一次 `/webhooks/github`，并安排一个 M 使用自己的 repo checkout 持续领取协调任务。把该 checkout 的本地身份设为 `TCA_AGENT_ID=agent-m` 后，共享 `AGENTS.md` 会自动加载 M 规则。这些都不需要每个成员重复配置。

每位成员第一次加入时只做三件事：

1. Clone 被测 repo，并配置自己的 GitHub 凭证。
2. 从项目发起者处领取共享 `TCA_API_TOKEN`，保存到 MCP 客户端的机器级环境或 secret store；按 `.tca/project.yaml` 配置原生 MCP 连接。
3. 为每个 Agent 工作区设置 `TCA_AGENT_ID`：A 使用唯一且稳定的 ID，M 使用保留值 `agent-m`。

A 工作区本地 `.env` 只需要身份：

```bash
TCA_AGENT_ID=agent-a1
```

以后成员只需像平常一样给 A 提需求。A 会按仓库里的 `AGENTS.md` 和 `.tca/protocol.md` 自动提交 Claim、等待、修改并完成；成员不需要手动生成 token、重复配置 webhook 或 checkout TCA Server 源码。

A 发布新 Claim 前必须先 fetch 远端默认分支。Claim 的 `base_commit` 是此刻默认分支的完整 SHA，工作分支固定为 `feature/<agent-id>/<claim-id>`。GitHub webhook 会验证基线新鲜且确实位于 Claim 分支历史中；Claim 修订只能改目标、范围和依赖，不能改身份、基线或工作分支。

## 接口先行、各自负责的并行协作

主管/P 可以只给每位 A 一个自然语言 feature（CooperBench 正是这种设定），而不提供文件、函数或实现边界。每个 A 先独立阅读自己 feature 所需的源码，再自行提交可修订的技术 Claim：`planned_paths`、`owned_symbols`、行为契约和临时验证。M 不替 A 拆技术任务；默认不同 Claim 并行，不要为了“协作”让 A2 无意义地等待 A1 全部完成。

若 A2 只依赖 A1 的一个稳定接口，A2 的 Claim 使用 `dependency_mode: after_interface` 与 `handoff_from: [a1-claim-id]`。A1 在该接口 commit 已可用时发布 handoff（commit、导出符号、行为契约），A2 就会带着“rebase 到该 commit、保持契约”的约束获批；此时 A1 仍可继续完成其余工作。最终提交保留 A1 的 commit 历史，A2 只对自己的增量负责。

接口所有者不得被静默覆盖：A2 若确实需要接管 A1 的符号，必须声明 `takes_over_symbols`，而 A1 的 handoff 必须显式把该符号列进 `transferable_symbols`；否则 C 会置为 `needs_human`。本地可运行完整状态流：

```bash
cd /path/to/tca-timeouts
./scripts/run_serial_handoff_demo.sh
```

`completed` 只表示工作分支已经 push 并可供集成，不表示它已经进入 `main`。completed Claim 会继续出现在 M 的协调上下文中，避免后续 Agent 从旧基线重复修改同一路径。集成者只需按团队流程 merge 并 push：GitHub 默认分支 webhook 会让 TCA 检查 completed 工作分支是否已进入该分支，并自动转为 `integrated`。

自动确认要求 merge 后仍保留工作分支提交历史，普通 merge commit 和 fast-forward 都满足。关闭 webhook、使用其它 Git provider，或 squash/rebase 导致 TCA 无法证明祖先关系时，管理员核验后可用 `integrate_claim` 兜底；普通 A 不负责这项 TCA 记账。

M 做路径级判断时可以只连 TCA MCP；做语义判断时，推荐直接运行在自己管理的被测 repo 只读 checkout 根目录。M 使用同一个团队 token，并通过本地 `TCA_AGENT_ID=agent-m` 自动获得协调角色；用户无需在每个 task 的 prompt 里重复说明 M 角色。

真实用户不需要 checkout 本仓库。A/M 使用 Codex、Claude、Cursor 等客户端的原生 MCP 配置，直接连接 `https://tca.example.com/mcp`。本仓库自带的 `tca-client` 只是维护者本地开发和 smoke test 的辅助命令。

M 不需要 TCA Server 源码；但如果 M 要做语义判断，它需要被测 repo 的只读上下文。推荐让 M 自己管理一份只读 checkout，不复用 A 的工作区；也可以用 Git provider 只读访问。只靠 TCA 返回的 Claim 元数据，只适合做路径级和依赖级判断。

完整原理和操作手册见 [docs/tca-guide.md](docs/tca-guide.md)。
