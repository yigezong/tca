# TCA 被测仓库模板

这个目录是一份普通代码仓库接入 Team Code Agent（TCA）所需的最小模板。

把这些文件复制到被测 repo 后，先修改 `.tca/project.yaml`。

## 模板包含什么

```text
.tca/project.yaml        项目标识和 TCA MCP 地址
.tca/protocol.md         A/M/C 协作协议
.tca/claim.example.yaml  Claim 示例
AGENTS.md                给开发 Agent 读的入口说明
.env.example             A 工作区本地身份模板
```

不要提交 `.env` 或真实 token。

## 1. 项目发起者：一次性接入

项目发起者把本模板复制到被测 repo，修改 `.tca/project.yaml`：

```yaml
version: 1
project_id: owner/repo

mcp:
  url: https://tca.example.com/mcp
  token_env: TCA_API_TOKEN
```

然后把模板文件提交到被测 repo 的默认分支。以后所有成员 clone repo 时都会得到项目标识、MCP 地址、协议和 Agent 规则，不需要各自重新填写。

在被测 GitHub repo 里添加一次 webhook：

- Payload URL：`https://<tca-server-host>/webhooks/github`
- Content type：`application/json`
- Secret：和 TCA Server 的 `GITHUB_WEBHOOK_SECRET` 一致
- Events：`push`

这个 webhook 属于项目，不属于成员。它既让 TCA 发现 `tca/claim/<agent-id>/<claim-id>` 分支，也在默认分支 push 后自动确认已集成的 Claim；只配置一次。

项目发起者还要：

1. 从 TCA Server 管理者处取得 MCP URL、共享 `TCA_API_TOKEN` 和 webhook secret。
2. 通过安全渠道把共享 `TCA_API_TOKEN` 发给获准加入项目的成员，不写进 repo 或聊天 prompt。
3. 安排一个 M 工作区持续领取本项目的协调任务。

如果团队自己部署 TCA Server，服务部署和 token 生成也只做一次；使用已有 TCA Server 时无需重复部署。

当前 TCA 使用 Server 级共享 token 和 webhook secret。一个 Server 虽能用 `project_id` 处理多个项目，但只适合同一信任范围，不提供互不信任团队之间的权限隔离。

## 2. 团队成员：每台机器一次

成员 clone repo 后：

1. 配置自己的 GitHub 凭证，确保 Agent 可以创建并 push Claim 分支和工作分支。
2. 从项目发起者处领取现成的 `TCA_API_TOKEN`。不要自行生成，因为当前团队内所有 A/M 使用同一个服务 token。
3. 把 token 保存到 MCP 客户端的机器级环境或 secret store，并按 `.tca/project.yaml` 中的 URL 配置原生 MCP 连接。

`token_env: TCA_API_TOKEN` 只是告诉客户端读取哪个环境变量，不包含 token，也不会自动生成 token。一台机器配置好后，在这台机器上参加同一 TCA 服务下的项目通常不用重复配置。

真实用户不需要安装或 checkout TCA Server 源码仓库。Agent 客户端必须能看到 TCA MCP tools；如果看不到，先修复机器级 MCP 配置，不要开始修改共享代码。

## 3. A 工作区：每个并行 Agent 一个身份

每个开发 Agent 都使用一份普通 clone 或 worktree。

从 `.env.example` 创建 `.env`：

```bash
cp .env.example .env
```

只填入这个工作区的稳定身份：

```text
TCA_AGENT_ID=agent-a1
```

`TCA_AGENT_ID` 是每个 Agent 工作区的稳定身份。并行 Agent 使用不同值，例如 `agent-a1`、`agent-a2`。

成员给 A 的 prompt 仍然只是普通产品需求，不需要写“你扮演 A”或逐步指挥 TCA。A 从仓库里的 `AGENTS.md` 得知协议，并使用当前工作区的 `TCA_AGENT_ID` 自动完成 Claim 流程。

## 4. 配置 M 工作区

M 是单独的协调 Agent，不复用 A 工作区。

如果 M 要做语义判断，它应自己 clone 一份被测 repo，并直接在这个 repo 根目录运行：

```bash
git clone https://github.com/owner/repo.git repo-for-m
cd repo-for-m
git remote set-url --push origin DISABLED
```

M 从当前 repo 的 `.tca/project.yaml` 读取 `project_id` 和 MCP URL，并从自己的 MCP 客户端读取同一个团队 `TCA_API_TOKEN`。M 不需要 `TCA_AGENT_ID`。

M 常驻循环调用：

```text
wait_for_coordination_task(project_id)
submit_coordination_decision(...)
```

如果 M 只有 Claim 元数据，它只能做路径级和依赖级判断。如果 M 要做语义判断，就只读查看自己这份 repo checkout，不提交、不 push。

## 5. 日常使用

完成上述一次性接入后：

- 项目发起者不需要为新任务重复配置 webhook、服务端环境变量或项目文件。
- 成员不需要为每个任务重新配置 MCP 或 token。
- 人只向 A 提正常需求；A 自己执行下面的 Claim 流程。
- M 保持 long-poll，自动领取需要协调的 Claim。

A 对每个人类需求执行：

1. `git fetch origin`，记录远端默认分支的完整 SHA 作为 `base_commit`。
2. 从该提交创建 `tca/claim/<agent-id>/<claim-id>`，并提交 `.tca/claim.yaml`。
3. Push Claim 分支。
4. 调用 `wait_for_decision(project_id, claim_id)`。
5. 只有 `allowed_actions` 包含 `start_execution` 时才开始。
6. 从 `base_commit` 创建并 push `work_branch`，分支名固定为 `feature/<agent-id>/<claim-id>`；先满足 Decision 约束，再修改代码。
7. 工作分支 push 后，调用 `complete_claim(project_id, claim_id)`。

GitHub webhook 会拒绝基于旧默认分支提交的新 Claim，也会拒绝 `base_commit` 不在 Claim 分支历史中的 Claim。Claim 后续递增 `version` 修改范围时，`author`、`base_commit` 和 `work_branch` 保持不变。

TCA 的 `completed` 不等于 merge 到默认分支。completed Claim 会继续参与协调，防止后续 Agent 忽略尚未进入基线的工作。PR、CI、merge 和最终产品取舍仍然走团队自己的集成流程；合并者只需正常 merge 和 push。GitHub 默认分支 webhook 会让 TCA 自动验证工作分支是否已进入提交历史，验证成功后转为 `integrated`。

普通 merge commit 和 fast-forward 可以自动确认。关闭 webhook、使用其它 Git provider，或 squash/rebase 改写提交历史时，由管理员核验后使用 `integrate_claim` 兜底；普通 A 不需要处理。
