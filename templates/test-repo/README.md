# TCA 被测仓库模板

这个目录是一份普通代码仓库接入 Team Code Agent（TCA）所需的最小模板。

把这些文件复制到被测 repo 后，先修改 `.tca/project.yaml`。

## 模板包含什么

```text
.tca/project.yaml        项目标识和 TCA MCP 地址
.tca/protocol.md         A/M/C 协作协议
.tca/claim.example.yaml  Claim 示例
AGENTS.md                给开发 Agent 读的入口说明
.env.example             A 工作区本地 secret 模板
```

不要提交 `.env` 或真实 token。

## 1. 配置被测 Repo

修改 `.tca/project.yaml`：

```yaml
version: 1
project_id: owner/repo

mcp:
  url: https://tca.example.com/mcp
  token_env: TCA_API_TOKEN
```

然后把模板文件提交到被测 repo 的默认分支。

## 2. 配置 GitHub Webhook

在被测 GitHub repo 里添加 webhook：

- Payload URL：`https://<tca-server-host>/webhooks/github`
- Content type：`application/json`
- Secret：和 TCA Server 的 `GITHUB_WEBHOOK_SECRET` 一致
- Events：`push`

这个 webhook 用来让 TCA 发现 `tca/claim/<agent-id>/<claim-id>` 分支的 push。

## 3. 配置 A 工作区

每个开发 Agent 都使用一份普通 clone 或 worktree。

从 `.env.example` 创建 `.env`：

```bash
cp .env.example .env
```

填入：

```text
TCA_API_TOKEN=...
TCA_AGENT_ID=agent-a1
```

`TCA_AGENT_ID` 是每个 Agent 工作区的稳定身份。并行 Agent 使用不同值，例如 `agent-a1`、`agent-a2`。

Agent 客户端必须能看到 TCA MCP tools。如果看不到，说明 MCP 没配好，Agent 必须停止共享代码修改并向人报告。

## 4. 配置 M 工作区

M 是单独的协调 Agent，不复用 A 工作区。

如果 M 要做语义判断，它应自己 clone 一份被测 repo，并直接在这个 repo 根目录运行：

```bash
git clone https://github.com/owner/repo.git repo-for-m
cd repo-for-m
git remote set-url --push origin DISABLED
```

M 从当前 repo 的 `.tca/project.yaml` 读取 `project_id` 和 MCP URL。M 还需要在自己的环境或 secret store 里提供 `TCA_API_TOKEN`。

M 常驻循环调用：

```text
wait_for_coordination_task(project_id)
submit_coordination_decision(...)
```

如果 M 只有 Claim 元数据，它只能做路径级和依赖级判断。如果 M 要做语义判断，就只读查看自己这份 repo checkout，不提交、不 push。

## 5. 一次正常 Claim 流程

A 对每个人类需求执行：

1. 创建 `tca/claim/<agent-id>/<claim-id>`。
2. 在 Claim 分支提交 `.tca/claim.yaml`。
3. Push Claim 分支。
4. 调用 `wait_for_decision(project_id, claim_id)`。
5. 只有 `allowed_actions` 包含 `start_execution` 时才开始。
6. 创建并 push `work_branch`，推荐 `feature/<agent-id>/<claim-id>`。
7. 工作分支 push 后，调用 `complete_claim(project_id, claim_id)`。

TCA 的 `completed` 不等于 merge 到 `main`。PR、CI、merge 和最终产品取舍仍然走团队自己的集成流程。
