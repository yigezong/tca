# TCA 指南

TCA 是一个 Git-first 的多 Agent 协作控制服务。它不替代 Git、PR 或 CI；它只在 Agent 改共享代码前，提供一个权威状态机，让团队知道每个 Agent 的意图、范围、冲突、依赖和放行条件。

本仓库是 TCA Server 源码仓库，不是被测 repo。当前不使用 TCA 自举开发本仓库，所以根目录不放 `.tca/`、`.codex/` 或其它活配置。被测 repo 接入模板在 `templates/test-repo/`。

## 1. 角色和边界

| 角色 | 运行位置 | 职责 |
|---|---|---|
| C / Controller | TCA Server，通常部署在 Vercel | 维护 Claim 状态机、校验状态转换、保存协调队列 |
| A / 开发 Agent | 用户本机、Codex task、CI worker | 提交 Claim，等 C 放行，执行代码修改，完成 Claim |
| M / 协调 Agent | 用户本机或团队机器，不默认云上常驻 | 领取待协调 Claim，结合 Claim 元数据和 repo 只读上下文判断冲突/依赖/风险，向 C 提交建议 |
| 集成者 | 人、集成 Agent 或 CI | 把 completed 工作分支合入目标分支，并确认 Claim 已 integrated |
| P / 人 | 人 | 提出目标，在机器无法自动裁决时决策 |
| Git provider | GitHub 等 | 保存 Claim 分支、工作分支和代码历史 |

关键边界：

- C 是唯一权威状态源。A/M 不能直接改数据库。
- M 不内置在 TCA Server 里。M 是外部 Agent，通过 MCP 调 C。
- TCA Server 保持轻量；昂贵的 Agent 不默认部署到云上。
- 当前没有 TCA 主动唤醒 Codex task。A/M 必须主动 long-poll；如果 Codex task 已经结束，TCA 不会让它自动复活。

```mermaid
flowchart LR
    A1["A1 本机 / Codex"] -->|"push Claim 分支"| Git["GitHub 被测 repo"]
    A2["A2 本机 / Codex"] -->|"push Claim 分支"| Git

    subgraph Server["TCA Server / C"]
      Webhook["POST /webhooks/github"] --> Controller["Controller"]
      MCP["/mcp"] --> Controller
      DebugAPI["GET /api/claims 调试查询"] -.-> Controller
      Controller <--> DB["Postgres / SQLite"]
    end

    Git -->|"push webhook"| Webhook
    A1 <-->|"MCP: wait/start/complete"| MCP
    A2 <-->|"MCP: wait/start/complete"| MCP
    M["M 本机 watch"] <-->|"MCP: wait task / submit decision"| MCP
```

## 2. 一次 Claim 的完整流程

```mermaid
sequenceDiagram
    participant A as A：开发 Agent
    participant Git as GitHub / 被测 repo
    participant C as TCA Server / C
    participant M as M：协调 Agent
    participant I as 集成者
    participant DB as 状态数据库

    A->>Git: push tca/claim/<agent-id>/<claim-id>
    Git->>C: POST /webhooks/github
    C->>Git: 读取 .tca/claim.yaml
    C->>DB: 保存 Claim，state=assessing
    C->>DB: queue coordination task
    M->>C: wait_for_coordination_task(project_id)
    C-->>M: Claim + active_claims + completed_claims
    M->>C: submit_coordination_decision(...)
    C->>DB: approved / waiting / blocked / needs_human
    A->>C: wait_for_decision(project_id, claim_id)
    C-->>A: state + allowed_actions
    A->>C: start_execution(project_id, claim_id)
    A->>Git: push work_branch
    A->>C: complete_claim(project_id, claim_id)
    C->>DB: state=completed（待集成）
    I->>Git: merge work_branch into target branch
    I->>C: integrate_claim(project_id, claim_id)
    C->>DB: state=integrated
```

A 的流程：

1. 从目标分支创建 `tca/claim/<agent-id>/<claim-id>`。
2. 在该分支提交 `.tca/claim.yaml`。
3. Push Claim 分支，让 GitHub webhook 通知 TCA。
4. 调用 `wait_for_decision(project_id, claim_id)`。
5. 只有 `allowed_actions` 包含 `start_execution` 时，调用 `start_execution`。
6. 从目标分支创建 `work_branch`，修改代码，提交并 push `work_branch`。
7. 工作分支已保存到 Git provider 后，调用 `complete_claim`。

`complete_claim` 只表示“这个 Claim 的工作分支已经完成并推到 Git provider”。它不表示代码已经进入 `main`。completed Claim 会继续出现在 M 收到的协调上下文里；如果后续 Claim 与它重叠，M 必须要求后续 A 先检查并纳入该 `work_branch`，或者返回 `needs_human`。不能因为前一个 Agent 已结束就假设它的代码已经进入基线。

进入 `main` 仍然要走团队自己的 PR、merge、CI 或人工集成流程。确认目标分支已经包含这项工作后，集成者调用 `integrate_claim(project_id, claim_id)`。只有 `integrated` 才会把该 Claim 从后续协调上下文移除。TCA 不自动猜测某次 push 是否等价于产品层面的集成。

M 的流程：

1. 启动一个已经配置好 TCA MCP 的 M Agent。
2. watch 循环调用 `wait_for_coordination_task(project_id)`。
3. 如果 C 把一个 Claim 交给 M，M 根据 active Claim、completed-but-unintegrated Claim、语义冲突和风险提交决策。
4. 与仍 active 的路径重叠时使用 `waiting`；只与 completed 工作重叠时，使用 `approved_with_constraints` 要求纳入其工作分支，无法安全集成时使用 `needs_human`。
5. 处理完一条后继续等下一条。

真实用户不需要 checkout 本仓库。A/M 应使用 Codex、Claude、Cursor 等客户端的原生 MCP 配置，直接连接 TCA Server 的 `/mcp`。本仓库里的 `tca-client` 只是维护者本地开发和 smoke test 的辅助命令，不是正式用户流程的一部分。

当前 smoke M 只是轻量 runner。它做的是路径级自动判断，不是完整语义审查。更复杂的 M 可以在同一个 MCP 协议上实现。

M 不需要 TCA Server 源码，但不能凭空做语义判断。`wait_for_coordination_task` 返回 Claim 元数据和两组协调上下文：`active_claims` 是尚未完成的工作，`completed_claims` 是已完成但尚未集成的工作。它足够判断“计划路径是否重叠、依赖是否还没完成”。如果要判断“两个目标语义上是否冲突、如何纳入 completed 工作分支”，M 还需要 repo 上下文。正式方案有三种：

| M 上下文来源 | 适合场景 | 边界 |
|---|---|---|
| M 自己管理的本地只读 checkout | 本机/团队机器常驻 M | M 可以读文件、看 commit、看历史，但不复用 A 的工作区，也不写业务 repo |
| Git provider 只读访问 | M 不想保存 checkout | M 通过 GitHub/GitLab API 读 `base_commit`、路径和历史 |
| TCA Server 提供上下文摘要 | 以后做托管 M 或轻客户端 | 需要扩展 C 的 API，把必要文件片段/diff 安全地给 M |

现在的实现只提供前两层的接口基础，没有把 repo 文件内容塞进 TCA 状态机。这样保持 C 轻量，也避免把代码库内容长期存进 TCA 数据库。

C 在 M 之前还有一层确定性路径规则：如果新 Claim 的 `planned_paths` 已经和仍 active 的 Claim 重叠，C 会直接把它放到 `waiting`，暂时不生成 M task。等阻塞 Claim completed 后，C 重新评估等待项；completed 工作不再形成硬等待，但会作为协调上下文交给 M，由 M 决定后续 A 如何纳入该工作。集成者确认 integrated 后，它才从上下文移除。

## 3. 状态机

Claim 的身份是 `(project_id, claim_id)`。不同项目可以复用同一个 `claim_id`。

状态：

| 状态 | 含义 | 允许动作 |
|---|---|---|
| `received` | C 收到 Claim，准备评估 | 无 |
| `assessing` | 等待 M 决策 | `get_claim_state` |
| `waiting` | 被其它 active Claim 阻塞 | `get_claim_state` |
| `approved` | 已获批 | `start_execution` |
| `approved_with_constraints` | 带约束获批 | `start_execution` |
| `needs_human` | 需要人处理 | `get_claim_state` |
| `blocked` | 被阻断 | `get_claim_state` |
| `executing` | A 正在执行 | `complete_claim` |
| `completed` | 工作分支已完成并 push，等待进入目标分支 | `integrate_claim` |
| `integrated` | 工作已进入目标分支 | 无 |

重要规则：

- `wait_for_decision` 会穿过 `received`、`assessing`、`waiting` 继续等，直到超时或出现最终可行动状态。
- `complete_claim` 后，C 会重新评估等待该 Claim 的其它 Claim，并把 completed 工作保留在 M 的协调上下文里。
- 只有目标分支已经包含 completed 工作时，集成者才能调用 `integrate_claim`。
- M 不能把 Claim 直接改为 `executing` 或 `completed`。
- A 没有 `start_execution` 权限时必须停止修改共享代码。

`waiting` 不是要求 A 立刻改 Claim。它的意思是：当前 Claim 暂时不能开始，因为 `wait_for` 里的其它 Claim 还 active。A 的默认动作是继续 `wait_for_decision`。当阻塞项 completed 后，C 会把等待中的 Claim 重新放回 `assessing`；M 会同时看到 completed Claim 的 `work_branch`，并决定要求 A 纳入该分支、继续等待集成，或交给人。只有当 A 想主动绕开冲突，比如缩小 `planned_paths` 或改依赖关系时，才需要提交新版 Claim，且 `version` 必须递增。

`wait_for_coordination_task(project_id)` 是给 M 用的取任务入口。它不是给 A 查自己状态的；A 用 `wait_for_decision`。M 调它时，C 会返回最早一个正在 `assessing` 的 Claim、同项目 `active_claims`，以及待集成的 `completed_claims`。M 根据这些信息提交 `approved`、`approved_with_constraints`、`waiting`、`blocked` 或 `needs_human`。

## 4. MCP/API 表面

当前保留的 MCP tools：

| Tool | 使用方 | 作用 |
|---|---|---|
| `submit_claim(claim)` | A / adapter | 非 GitHub 场景直接提交 Claim |
| `get_claim_state(project_id, claim_id)` | A/M | 查询权威状态、决策和 allowed_actions |
| `wait_for_decision(project_id, claim_id)` | A | 等待 Claim 离开中间等待态 |
| `start_execution(project_id, claim_id)` | A | 把获批 Claim 转为 `executing` |
| `complete_claim(project_id, claim_id)` | A | 把执行中的 Claim 转为 `completed` |
| `integrate_claim(project_id, claim_id)` | 集成者 | 确认 completed 工作已进入目标分支，转为 `integrated` |
| `wait_for_coordination_task(project_id)` | M | 等待待协调 Claim；`project_id=""` 表示所有项目 |
| `submit_coordination_decision(...)` | M | 提交 M 的协调建议，由 C 校验并落库 |

HTTP API 只保留调试/检查用状态查询。它不是第二套协议，也不是 A/M 的正式入口；正式入口是 MCP。

```text
GET /health
GET /api/claims/<claim_id>?project_id=<owner/repo>
POST /webhooks/github
POST /mcp
```

`/health` 不需要 token。`/api/*` 和 `/mcp` 需要：

```text
Authorization: Bearer <TCA_API_TOKEN>
```

GitHub webhook 用 `GITHUB_WEBHOOK_SECRET` 签名校验，不使用 `TCA_API_TOKEN`。

`/api/claims/<claim_id>` 当前只等价于一次 HTTP 版 `get_claim_state`，方便人用 `curl` 或浏览器排查线上状态。如果以后觉得它增加理解成本，可以删掉；核心流程不依赖它。

## 5. 团队接入和配置归属

先区分三种身份：TCA Server 管理者负责服务，项目发起者负责把一个 repo 接入服务，团队成员负责自己的开发环境。一个人可以兼任，但配置仍然只做在它所属的那一层。

| 配置 | 谁创建或设置 | 作用范围 | 放在哪里 | 是否提交 |
|---|---|---|---|---|
| TCA Server 环境变量 | Server 管理者 | 一个 TCA 服务 | Vercel / Server env | 否 |
| `.tca/`、`AGENTS.md`、模板 README | 项目发起者 | 一个被测 repo | 被测 repo | 是 |
| GitHub webhook | 项目发起者 | 一个被测 repo | GitHub repo Settings | 否 |
| `TCA_API_TOKEN` | Server 管理者生成，成员只领取 | 当前为一个服务/团队共享 | MCP 客户端 secret store 或机器级环境 | 否 |
| 原生 MCP 连接 | 每位成员或机器管理员 | 一台机器/一个客户端 | Codex、Claude、Cursor 等客户端配置 | 否 |
| `TCA_AGENT_ID` | 每个 A 工作区设置 | 一个 A clone/worktree | 工作区 `.env` 或本地环境 | 否 |
| GitHub 凭证 | 每位成员 | 一个 GitHub 用户 | credential manager / SSH agent | 否 |
| M 的 repo checkout | 运行 M 的人或团队机器 | 一个项目的 M | M 自己的本地目录 | 否 |

当前极简鉴权模型只有一个 Server 级共享 `TCA_API_TOKEN`。它表示“允许访问这个 TCA Server”，不是个人或项目身份：Server 管理者生成一次，获准访问该实例的成员领取同一个值。成员不应自行生成 token。A 的身份来自 `TCA_AGENT_ID`，Git push 的人员权限来自各自的 GitHub 凭证。

`project_id` 可以让一个 Server 同时保存多个项目的独立 Claim 状态，但当前 token、`GITHUB_WEBHOOK_SECRET` 和 `GITHUB_TOKEN` 都是 Server 级配置，不构成多租户权限隔离。因此当前实例只适合同一信任范围内的多个项目；互不信任的团队要共用公共服务，必须先增加项目级凭证、授权和审计。

### TCA Server

最小部署变量：

```bash
TCA_API_TOKEN=replace-with-random-token
DATABASE_URL=replace-with-postgres-url
TCA_ALLOWED_HOSTS=tca.example.com
```

启用 GitHub webhook 时再加：

```bash
GITHUB_WEBHOOK_SECRET=replace-with-webhook-secret
GITHUB_TOKEN=replace-with-github-token
```

说明：

- `TCA_API_TOKEN`：Server 管理者生成一次的共享 Bearer token；A/M 调 `/mcp`、人用 `/api` 调试时复用它。
- `DATABASE_URL`：线上 Postgres 连接串；未设置时本地 SQLite。
- `TCA_ALLOWED_HOSTS`：MCP transport 的 Host header 白名单，填 TCA Server 域名，不带 `https://`。
- `GITHUB_WEBHOOK_SECRET`：GitHub webhook secret；GitHub 用它签名，TCA 用它验签。
- `GITHUB_TOKEN`：TCA 从 GitHub Contents API 读取 `.tca/claim.yaml`。

### 项目发起者：一次性接入 repo

如果团队已有可用的 TCA Server，项目发起者不需要部署 `proj_tca`。只需：

1. 从 Server 管理者处取得 MCP URL、共享 `TCA_API_TOKEN` 和 webhook secret。
2. 把 `templates/test-repo/` 复制进被测 repo。
3. 修改 `.tca/project.yaml`，并把模板文件提交到默认分支。
4. 在被测 GitHub repo 配置一次 webhook。
5. 通过安全渠道把共享 token 发给获准成员。
6. 安排一个 M 在自己的 repo checkout 中持续等待协调任务。

`.tca/project.yaml`：

```yaml
version: 1
project_id: owner/repo
mcp:
  url: https://tca.example.com/mcp
  token_env: TCA_API_TOKEN
```

这里提交的是 MCP URL 和环境变量名，不是 token 的真实值。所有成员 clone 后自动得到这份项目配置。

Claim 文件固定为 `.tca/claim.yaml`。

Claim 分支固定为：

```text
tca/claim/<agent-id>/<claim-id>
```

`<agent-id>` 必须等于 `.tca/claim.yaml` 的 `author`。`<claim-id>` 必须等于 `.tca/claim.yaml` 的 `claim_id`。

Webhook 的 Payload URL、secret 和事件选择见第 6 节。一个 repo 只配置一次，不随成员或任务重复配置。

### 团队成员：每台机器一次

成员第一次加入时：

1. Clone 被测 repo，并配置自己的 GitHub 凭证。
2. 从项目发起者处领取共享 `TCA_API_TOKEN`，保存到 MCP 客户端的机器级环境或 secret store。
3. 按 `.tca/project.yaml` 中的 URL 配置客户端的原生 MCP 连接，并让 Bearer token 从 `TCA_API_TOKEN` 读取。
4. 确认客户端能看到 TCA MCP tools。

`token_env: TCA_API_TOKEN` 只是在声明环境变量名。它不会生成 token，也不意味着每个成员应该创建一个新 token。真实 token 不进入 repo、prompt 或日志。

当前还没有从 `.tca/project.yaml` 自动写入各客户端配置的正式命令，因此第 3 步需要在每台机器上做一次。后面的“原生 MCP 配置生成”是计划中的简化功能。

成员不需要 checkout TCA Server 源码，不需要配置被测 repo webhook，也不需要接触 `DATABASE_URL`、`GITHUB_TOKEN` 或 `GITHUB_WEBHOOK_SECRET`。

### A 工作区：每个并行 Agent 一个身份

A 在被测 repo 工作区运行，默认从 `.tca/project.yaml` 读取 `project_id` 和 MCP URL。

每个 A clone/worktree 从 `.env.example` 创建自己的 `.env`，只设置身份：

```bash
TCA_AGENT_ID=agent-a1
```

并行 A 使用不同的稳定值，例如 `agent-a1`、`agent-a2`。共享 token 已由机器级 MCP 客户端提供，不必复制到每个工作区。

成员日常只给 A 普通产品需求，不需要在 prompt 中说“扮演 A”或手动指导 Claim 步骤。A 读取 repo 中的 `AGENTS.md` 和 `.tca/protocol.md` 后自行提交 Claim、等待放行、执行、push 并完成 Claim。

A 的客户端必须能看到 TCA MCP tools。看不到时不要开始改共享代码，也不要自己生成 token；应报告机器级 MCP 配置未完成。

### M：项目级协调工作区

M 不需要 TCA Server 源码 checkout。推荐让 M 自己 clone 一份被测 repo，并直接以这个 repo 根目录作为 M task 的工作目录。这样 M 要看代码上下文时读当前目录就行，不需要额外路径变量。`project_id` 和 MCP URL 从当前 repo 的 `.tca/project.yaml` 读取。

M 的 MCP 客户端使用同一个团队 `TCA_API_TOKEN`，但 M 不需要 `TCA_AGENT_ID`。M 是项目级能力，不要求每位开发成员都运行一份；团队可以指定一台本机或团队机器常驻 M。

如果 M 只做路径级判断，可以只看 `wait_for_coordination_task` 返回的 Claim 元数据。如果 M 要做语义判断，就在自己的 repo checkout 里读文件、看 commit、比较 active claims 的目标和路径。不要让 M 复用 A 的工作区，因为 A 的工作区会有未完成修改、分支切换和测试副产物。

真实 M 的循环是 MCP tool 调用，不是 shell 命令：

1. 调 `wait_for_coordination_task(project_id)` 等待任务。
2. 看 Claim、active claims、目标和风险。
3. 调 `submit_coordination_decision(...)` 提交判断。
4. 继续等下一条。

本仓库维护者做 smoke test 时，当前还可以临时用仓库内置 CLI 充当 MCP 客户端；正式 smoke 应该让 M task 直接使用客户端原生 MCP tools。

### Long-poll 和主动唤醒

当前 TCA 用 long-poll：A/M 主动发一个“等一会儿”的请求给 C；如果这段时间内有状态变化，C 立即返回；如果没有变化，到超时时间后返回当前状态或空任务。客户端再发下一次请求。

主动唤醒是另一种模型：A/M 可以退出或睡死，C 在状态变化时主动调用某个回调、消息队列、Webhook、系统通知或 Codex task API，把对应 Agent 重新叫起来。主动唤醒更自动，但也更重：要维护回调地址、身份、重试、离线恢复、权限和队列。当前极简版本不做它，所以 M 要常驻调用 `wait_for_coordination_task`，A 要在自己的 task 里持续 `wait_for_decision`。

### 原生 MCP 配置生成

从 `.tca/project.yaml` 生成 Codex / Claude / Cursor 的 MCP 配置是有价值的，而且应该作为短期小功能做。它不需要进入 C 的状态机，只需要一个本地命令读取：

```yaml
mcp:
  url: https://tca.example.com/mcp
  token_env: TCA_API_TOKEN
```

然后输出各客户端需要的配置片段。注意真实 token 不应该写进生成文件，应该继续使用环境变量引用或让客户端自己的 secret 机制保存。

这件事应该优先做，因为它正好解决“用户机器没有 `proj_tca`”的问题。实现时可以加成一个独立的小生成器，例如 `tca mcp-config --target codex|claude|cursor`，只生成配置文本，不改用户全局配置。

## 6. GitHub 和 Vercel 怎么分清

`fringsoo/tca` 是 TCA Server 源码 repo。它和 Vercel 的关系是：

```text
fringsoo/tca push -> Vercel Git integration -> 部署 TCA Server
```

这条链路不需要在 `fringsoo/tca` 自己的 GitHub Webhooks 里配置 `/webhooks/github`。

`/webhooks/github` 是给被测 repo 用的：

```text
被测 repo push tca/claim/<agent-id>/<claim-id>
  -> GitHub webhook
  -> https://tca.example.com/webhooks/github
```

Webhook 配置：

- Payload URL：`https://<tca-server-host>/webhooks/github`
- Content type：`application/json`
- Secret：和 Server env `GITHUB_WEBHOOK_SECRET` 相同
- Events：`push`

## 7. 非 GitHub 场景

CooperBench 或其它非 GitHub git server 不走 `/webhooks/github`。它们可以直接通过 MCP `submit_claim(claim)` 提交同一份 Claim 数据。

这时仍然使用同一个状态机：

```text
submit_claim -> assessing -> M decision -> wait/start/complete -> integrate
```

## 8. 当前文件地图

根目录：

| 文件 | 作用 |
|---|---|
| `README.md` | 项目入口和最小部署/使用说明 |
| `AGENTS.md` | 本仓库 Agent 工作规则 |
| `.env.example` | TCA Server 环境变量模板 |
| `.gitignore` | 忽略本地环境、缓存、数据库和 secret |
| `app.py` | Vercel ASGI 入口 |
| `vercel.json` | Vercel 函数配置 |
| `pyproject.toml` | Python 项目、依赖、命令和测试配置 |
| `uv.lock` | 依赖锁 |
| `templates/test-repo/` | 被测 repo 接入模板 |

源码：

| 文件 | 作用 |
|---|---|
| `src/tca/app.py` | Starlette app，挂载 `/health`、`/api`、`/mcp`、`/webhooks/github` |
| `src/tca/mcp_server.py` | MCP tools |
| `src/tca/client_cli.py` | 维护者本地开发和 smoke test 用 CLI |
| `src/tca/controller.py` | 状态机和协调队列逻辑 |
| `src/tca/domain.py` | Claim、Decision、状态和 allowed_actions |
| `src/tca/rules.py` | C 侧确定性路径冲突检查 |
| `src/tca/github.py` | GitHub webhook 验签、Claim 分支解析、Claim 文件读取 |
| `src/tca/store.py` | SQLite store 和 store 构造入口 |
| `src/tca/postgres_store.py` | Postgres store |
| `src/tca/store_protocol.py` | Store 协议接口 |

测试：

| 文件 | 作用 |
|---|---|
| `tests/test_app.py` | HTTP 鉴权和 app 创建 |
| `tests/test_client_cli.py` | CLI 配置解析和 watch 决策 |
| `tests/test_controller.py` | 状态机 |
| `tests/test_github.py` | GitHub adapter |
| `tests/test_mcp_server.py` | MCP tools |
| `tests/test_store.py` | SQLite 迁移 |

## 9. 当前不做的东西

按极简原则，当前没有：

- TCA 自举配置：本仓库根目录不放 `.tca/` 或 `.codex/`。
- 产品级 M 服务：当前只有外部 M Agent 通过 MCP long-poll。
- TCA 主动唤醒 Codex task：当前是 A/M 主动 long-poll。
- heartbeat、progress、abandon 等额外 tool。
- 自定义 Claim 分支前缀或 Claim 文件路径。
- GitHub status check / CI 门禁。
- 多用户 token、项目级权限和审计后台。
