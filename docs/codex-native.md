# 在现有 Codex 中使用 TCA

这一接入保留 Codex 原本的聊天、终端、代码视图、登录和模型设置。TCA 只增加协调工具及仓库工作流；**本地模式不需要 OpenRouter/OpenAI API key、TCA 服务器、Postgres、webhook 或演示网页**。Codex 本身仍需正常登录，Git 仓库是协作前提。

## 第一次安装（macOS / Linux）

当前尚未发布安装包，因此先取得本仓库源码。在 TCA 源码根目录执行：

```bash
sh scripts/install-codex.sh /absolute/path/to/your-git-repo
```

脚本检测 Codex 与 Git；若缺少 `uv`，用其官方安装器安装。`uv` 随后将 TCA 及 Python 3.12 安装到隔离环境，不改项目的 Python 依赖。完成后运行 `tca-codex doctor --repo /absolute/path/to/your-git-repo`。也可手动运行 `uv tool install --python 3.12 .` 和 `tca-codex init --repo ...`。

初始化只会在业务仓库加入：

- `.tca/project.yaml`：公开项目 ID 和本地/远程模式；
- `.tca/agent-id`：当前工作树身份，已加入 `.gitignore`；
- `.agents/skills/tca-coordination/SKILL.md`：Codex 工作流；
- `AGENTS.md` 中一个有标记的短段落：提醒 Codex 先读上述规则。

它还用 Codex 自带的 `codex mcp add` 注册名为 `tca_local` 的本地 stdio 工具，与你已有的远程 `tca` 连接并存。所有角色在同一台电脑上共享 `~/.tca/coordination.db`；不同项目以 `project_id` 区分。不会读取或改动 Codex 的模型、登录凭证。若机器已有不同的 `tca_local` MCP 配置，安装会停下；确认需要替换后才加 `--replace-mcp`。

安装完成后，把 `AGENTS.md`、`.agents/skills/tca-coordination/SKILL.md`、`.tca/project.yaml`、`.gitignore` 提交进业务仓库。打开该仓库的 Codex，照常描述开发任务即可。Codex 会根据项目规则提交 Claim、读取决策、开发和报告完成。

## 一台电脑使用一个 A 和一个 M

一台电脑只绑定一个开发者 A（使用该电脑配置的 GitHub 身份）；同一台电脑可以再运行一个 M。M 只读取 Claim、代码和 Git 状态并发布建议，不提交业务代码。A 与 M 使用同一份本地 SQLite 协调库，但各自在独立工作树中运行，以隔离身份和文件修改。左侧项目应代表业务 repo；A/M 是该 repo 下的 Codex 对话。

先提交共享配置，再为 M 创建只读 Git worktree，并分别初始化身份：

```bash
sh scripts/install-codex.sh /absolute/path/to/a-worktree --agent-id agent-a
sh scripts/install-codex.sh /absolute/path/to/m-worktree --agent-id agent-m
```

在 Codex 的同一个 repo 项目下打开 A 和 M 两个对话，分别选择对应工作树。给 A 正常的开发需求；当 Claim 需要协同时，在 M 对话中说“处理本项目待协调的 TCA Claim”。M 是普通 Codex 任务，需要有人启动；当前版本不会在后台自动唤醒 M。A 可以继续使用 Codex 原有模型、终端、代码视图和登录。完成后的分支照常由人或团队流程合并；`complete_claim` 不等于已合并。

多名开发者分别位于不同电脑时，每台电脑仍只运行一个 A；他们通过共享 TCA Server 协作。不要在单台机器上用两个 A 模拟两个 GitHub 身份。

### Claim 到达时机

若协作组声明 `expected_group_size > 1`，且尚无人开始执行，C 会先收齐该组的 Claim，再把每份 Claim 分别交给 M。M 查看完整组的需求与范围，并分别给每位 A 发布建议和决策；所有组内 Claim 都离开 `assessing` 后，A 才能开始。若 A1 已获批但还没开工时 A2 到达，C 会重新打开 A1 的协调状态，让 M 同时看两份 Claim。若 A1 已经处于 `executing`，后来到达的 A2 不会让 C 暂停或重写 A1；C 只为 A2 建立协调任务，M 的建议只发给 A2。

因此组内 A 应使用相同的 `coordination_group`。若在开工前就知道有两名参与者，两份 Claim 都应声明 `expected_group_size: 2`；若第二份 Claim 是后来才出现，A2 可以把预期组大小更新为 `2`。C 会按状态转换时序处理：A1 开工前两份都纳入 M 的协调；A1 已开工后只协调 A2。独立任务应使用默认的组大小 `1`。

## 跨电脑团队

本地 SQLite 只在一台电脑上共享状态，不能给跨电脑成员使用。跨电脑团队应部署共享服务，设置 `TCA_AUTH_MODE=github`、`DATABASE_URL` 和 `TCA_ALLOWED_HOSTS`。所有者和开发者都使用自己的 GitHub 账号；服务端每次操作都会重新检查仓库权限。

在 TCA 源码目录运行 `uv tool install --python 3.12 .` 安装 CLI。项目所有者先登录 GitHub，并在自己的 repo 工作树中启动项目：

```bash
gh auth login
export TCA_GITHUB_TOKEN="$(gh auth token)"
tca-codex start --repo /absolute/path/to/repo \
  --server-url https://your-tca-server.example.com/mcp
```

所有者必须有 repo admin 权限，启动后是这个项目的 M。可针对自己有 admin 权限的不同 repo 重复 `start`。M 只读代码，不提交业务代码。
也可以不用先 clone，执行 `tca-codex start --project-id owner/repo --server-url https://your-tca-server.example.com/mcp`；之后 clone 并用 `tca-codex join --repo /path/to/repo --server-url https://your-tca-server.example.com/mcp` 配置 M 工作树。

其他开发者无需等待 TCA 邀请。他们使用自己的 GitHub 登录，在各自电脑上查看可进入的已启动项目、clone 对应 repo 后加入：

```bash
gh auth login
export TCA_GITHUB_TOKEN="$(gh auth token)"
tca-codex projects --server-url https://your-tca-server.example.com/mcp
tca-codex join --repo /absolute/path/to/cloned-repo \
  --server-url https://your-tca-server.example.com/mcp
tca-codex doctor --repo /absolute/path/to/cloned-repo
```

加入要求 repo 写入权限；服务端从 GitHub login 固定生成 `agent-<login>`，因此同一个开发者只有一个 A 身份。修改 `.tca/agent-id` 不能冒充另一名 A 或 M。Codex 启动时也必须能读取 `TCA_GITHUB_TOKEN`；若桌面 App 没继承终端环境变量，要把变量放到 App 的启动环境中。项目共享配置可提交到业务 repo，`.tca/agent-id` 则是每个工作树本地文件。
若 repo 已初始化为本地模式，给 `start` 或 `join` 加 `--replace-local-config`，以显式迁移同一项目的配置和该工作树身份。

旧式受信任团队仍可使用共享 token 模式：

```bash
tca-codex init --repo /absolute/path/to/repo --mode remote \
  --server-url https://your-tca-server.example.com/mcp --agent-id agent-a1
```

共享 token 模式没有项目级鉴权，只适合同一信任范围。推荐的 GitHub 模式用个人 `TCA_GITHUB_TOKEN`，同样**不是模型 API key**。GitHub 模式通过认证 MCP 提交 Claim；旧 webhook 的 Claim 入口被关闭，签名的默认分支 push 仍可确认集成。

## 当前边界

- 这是原生接入的首版；源代码安装仍需下载 TCA 仓库。公开发布后可把安装脚本指向版本化包或二进制。
- 安装脚本目前覆盖 macOS/Linux；Windows 需相应安装入口。
- 本地模式不依赖 GitHub webhook，Claim 由 Codex 直接通过 MCP 提交；它只证明本机多工作树的协调流程。
- 本机使用模式最多绑定一个 A，可再配一个只读 M；跨电脑 A 成员必须连接共享 TCA Server。
- 首次设置后重开 Codex 任务，让新 MCP 与仓库规则进入新会话。
