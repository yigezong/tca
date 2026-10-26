# 在现有 Codex 中使用 TCA（本地预览版）

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

## 在同一台电脑上试两名开发者和 M

先提交上述共享配置，再为 A2 和 M 创建普通 Git worktree。在每份工作树中运行同一个安装入口，并指定不同身份：

```bash
sh scripts/install-codex.sh /absolute/path/to/a2-worktree --agent-id agent-a2
sh scripts/install-codex.sh /absolute/path/to/m-worktree --agent-id agent-m
```

分别在 Codex 打开 A1、A2、M 工作树。向 A1/A2 给出各自的自然语言需求；向 M 说“处理本项目待协调的 TCA Claim”。M 是普通 Codex 任务，需要有人启动；当前版本不会在后台自动唤醒 M。两位开发者仍使用各自原有的 Codex 模型和终端。完成后的分支照常由人或团队流程合并；`complete_claim` 不等于已合并。

## 跨电脑团队

本地 SQLite 只在一台电脑上共享状态，不能给跨电脑成员使用。已有受信任团队可让管理员部署现有 TCA Server，再在每台电脑运行：

```bash
tca-codex init --repo /absolute/path/to/repo --mode remote \
  --server-url https://your-tca-server.example.com/mcp --agent-id agent-a1
```

远程模式使用 `TCA_API_TOKEN` 环境变量连接 TCA，**这不是模型 API key**。当前服务仍是团队共享 token，缺少跨团队的项目级授权；它不能直接作为公开多租户服务。跨电脑的无管理员、一键加入还需要做项目邀请与授权。

## 当前边界

- 这是原生接入的首版；源代码安装仍需下载 TCA 仓库。公开发布后可把安装脚本指向版本化包或二进制。
- 安装脚本目前覆盖 macOS/Linux；Windows 需相应安装入口。
- 本地模式不依赖 GitHub webhook，Claim 由 Codex 直接通过 MCP 提交；它只证明本机多工作树的协调流程。
- 首次设置后重开 Codex 任务，让新 MCP 与仓库规则进入新会话。
