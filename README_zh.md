# Team Code Agent

Team Code Agent（TCA）是一个 Git-first 的多 Agent 协作控制服务。它只做一件事：在多个 Agent 修改同一个 repo 前，把“谁准备改什么、是否冲突、什么时候可以开始、什么时候完成”变成一个共享的权威状态。

本仓库是 TCA Server 源码仓库，不是被 TCA 管理的业务 repo。本仓库自身不使用 TCA 自举，因此根目录不保存 `.tca/` 或 `.codex/` 活配置。被测 repo 的接入模板在 `templates/test-repo/`。

想在已有 Codex 中直接使用 TCA，可先看 [Codex 原生接入](docs/codex-native.md)：单机预览使用本地 MCP + SQLite；多人跨电脑使用 GitHub 身份与共享 TCA Server。两者都沿用 Codex 的登录和模型设置，不需要模型 API key。

英文说明见 [README.md](README.md)。[TCA 流程演示视频](docs/assets/tca-process-demo.mp4) 展示了协调、并行开发、Git 集成和联合验收；其中也保留了实际冲突及修复过程。

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

启动后打开 `http://localhost:8000/` 可查看项目主页，并按项目 ID、Claim ID 查询实时状态。旧式共享模式使用 `TCA_API_TOKEN`；GitHub 模式使用当前用户的 GitHub token。页面仅在本次请求中使用输入的 token，不会保存它。

## 部署 TCA Server

多人跨电脑使用时，在部署环境设置：

```bash
TCA_AUTH_MODE=github
DATABASE_URL=replace-with-postgres-url
TCA_ALLOWED_HOSTS=tca.example.com
```

服务端用每个人自己的 GitHub token 验证身份和 repo 权限。项目发起者需要 repo 的 admin 权限；他是该项目唯一的 M。其他有写入权限的成员可自行加入，各自固定为 `agent-<GitHub login>`，每人在同一项目同时最多一个活跃 Claim。不能靠修改本地 `agent-id` 冒充另一名 A 或 M。权限撤销后，后续 TCA 操作也会被拒绝。权限字段来自 [GitHub 仓库接口](https://docs.github.com/en/rest/repos/repos)。
如需默认分支 push 后自动确认集成，可额外配置 `GITHUB_WEBHOOK_SECRET`、`GITHUB_TOKEN`，并在业务 repo 设置签名 webhook；不配置时由 M 核验后调用 `integrate_claim`。

CLI 尚未发布独立安装包。每个人先取得本仓库源码、安装 CLI，并让 Codex 启动环境可读取自己的 `TCA_GITHUB_TOKEN`：

```bash
git clone https://github.com/yigezong/tca.git
cd tca
uv tool install --python 3.12 .
gh auth login
export TCA_GITHUB_TOKEN="$(gh auth token)"
```

**项目所有者启动任意有 admin 权限的 repo：**

```bash
tca-codex start --repo /absolute/path/to/your-git-repo \
  --server-url https://tca.example.com/mcp
tca-codex doctor --repo /absolute/path/to/your-git-repo
```

`start` 从 repo 的 GitHub `origin` 得到 `owner/repo`，向服务端注册项目，并把当前工作树配置成 `agent-m`。M 只读代码、处理协调任务，不提交业务代码。不同 repo 可以分别启动；M 的 Codex 对话需要有人打开，不是后台守护进程。

不想先 clone 时，所有者也可以直接执行 `tca-codex start --project-id owner/repo --server-url https://tca.example.com/mcp`；之后 clone 该 repo，再用 `tca-codex join --repo /path/to/repo --server-url https://tca.example.com/mcp` 配置自己的 M 工作树。

**项目开发者自主查看和加入：**

```bash
tca-codex projects --server-url https://tca.example.com/mcp
tca-codex join --repo /absolute/path/to/cloned-repo \
  --server-url https://tca.example.com/mcp
tca-codex doctor --repo /absolute/path/to/cloned-repo
```

`projects` 只列出当前 GitHub 账号有权进入的已启动项目。`join` 要求 repo 写入权限，自动绑定这个 GitHub 账号唯一的 A 身份。每位开发者在自己的电脑和工作树中使用 Codex 正常开发；一台电脑只运行一个会提交代码的 A。所有者可以提交共享的 `AGENTS.md`、`.agents/skills/` 和 `.tca/project.yaml`；每个工作树自己的 `.tca/agent-id` 会被 Git 忽略。`TCA_GITHUB_TOKEN` 是仓库鉴权凭证，不是模型 API key，不要提交进 Git。Codex 桌面版若从 Dock 启动且读不到终端变量，需要在其启动环境中配置该变量；`doctor` 会检查连接和身份。

GitHub 项目模式通过已认证 MCP 直接提交 Claim，不接受旧 webhook 的 Claim 提交。若配置签名 webhook，默认分支 push 仍可自动确认集成。当前单机 `tca_local` 模式仍是无跨用户鉴权的预览，不能代替上述共享服务。

若 repo 已按本地预览模式初始化，`start` 或 `join` 后加 `--replace-local-config`，可将同一个项目的 `.tca/project.yaml` 和当前工作树的 `.tca/agent-id` 转到远程模式；其它有差异的文件仍不会被覆盖。所有者应在单独的 M 工作树中执行转换。

### 旧式共享 token 模式

Vercel 部署本仓库即可。TCA Server 源码仓库自身不需要配置 `/webhooks/github`；那个 webhook 是给被测 repo 用的。

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

## 旧式共享 token 接入

旧模式的 TCA Server 管理者只部署一次服务，并生成一个 `TCA_API_TOKEN`。成员共享这个服务访问密钥。`DATABASE_URL`、`GITHUB_TOKEN`、`GITHUB_WEBHOOK_SECRET` 等服务端变量也只由管理者配置。

共享 token 模式没有项目级权限隔离，只适合同一信任范围；跨团队使用应启用上面的 GitHub 项目模式。

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

接口所有者不得被静默覆盖：A2 若确实需要接管 A1 的符号，必须声明 `takes_over_symbols`，而 A1 的 handoff 必须显式把该符号列进 `transferable_symbols`；否则 C 会置为 `needs_human`。协议细节见 [操作手册](docs/tca-guide.md)。

`completed` 只表示工作分支已经 push 并可供集成，不表示它已经进入 `main`。completed Claim 会继续出现在 M 的协调上下文中，避免后续 Agent 从旧基线重复修改同一路径。集成者只需按团队流程 merge 并 push：GitHub 默认分支 webhook 会让 TCA 检查 completed 工作分支是否已进入该分支，并自动转为 `integrated`。

自动确认要求 merge 后仍保留工作分支提交历史，普通 merge commit 和 fast-forward 都满足。关闭 webhook、使用其它 Git provider，或 squash/rebase 导致 TCA 无法证明祖先关系时，管理员核验后可用 `integrate_claim` 兜底；普通 A 不负责这项 TCA 记账。

M 做路径级判断时可以只连 TCA MCP；做语义判断时，推荐直接运行在自己管理的被测 repo 只读 checkout 根目录。GitHub 模式下 M 使用项目所有者自己的 GitHub 身份，旧式模式下才使用共享团队 token。M 不应复用 A 的工作树。

目前 CLI 尚未发布成独立安装包，用户首次安装仍需取得本仓库源码；之后 A/M 使用 Codex 等客户端的原生 MCP 配置连接 `https://tca.example.com/mcp`。本仓库自带的 `tca-client` 只是维护者本地开发和 smoke test 的辅助命令。

M 不需要 TCA Server 源码；但如果 M 要做语义判断，它需要被测 repo 的只读上下文。推荐让 M 自己管理一份只读 checkout，不复用 A 的工作区；也可以用 Git provider 只读访问。只靠 TCA 返回的 Claim 元数据，只适合做路径级和依赖级判断。

完整原理和操作手册见 [docs/tca-guide.md](docs/tca-guide.md)。
