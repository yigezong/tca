# TCA 协作协议

## 角色

- **A（开发 Agent）**：提出 Claim、获批后执行代码修改，并在完成时通知 C。
- **M（协调 Agent）**：从 C 获取待协调任务，理解语义冲突并提交建议。
- **C（协议控制器）**：运行在 TCA Server 中，维护状态机、校验操作并保存 Claim 状态和协调队列。
- **P（人）**：提出目标；在目标或权限无法自动裁决时作出决定。

A 和 M 都是 TCA Server 之外的客户端，都通过 MCP 与 C 交互。

## A 的最短流程

1. `git fetch origin`，把远端默认分支当前的完整 commit SHA 记为 `base_commit`，并从该提交创建 `tca/claim/<agent-id>/<claim-id>` 分支。
2. 在该分支提交 `.tca/claim.yaml`，说明目标、工作分支、预计路径和依赖；新 Claim 的 `version` 是 `1`。
3. Push Claim 分支；Git provider webhook 调用 `/webhooks/github` 通知 C。
4. 调用 `wait_for_decision(project_id, claim_id)`。只有 `allowed_actions` 包含 `start_execution` 时才能开始。
5. 调用 `start_execution(project_id, claim_id)` 后，从 Claim 的 `base_commit` 创建 `work_branch`，先满足 Decision 中的约束，再修改代码。
6. 修改完成后提交并 push `work_branch`。
7. 范围变化时停止越界修改，并通过 Claim 分支提交新版 `.tca/claim.yaml`；完成后调用 `complete_claim(project_id, claim_id)`。

`complete_claim` 只表示工作分支已 push、可以集成，不表示已经进入目标分支。A 如果收到与 `state=completed` Claim 重叠的约束，必须先检查并纳入其 `work_branch`；无法安全处理时停止并交给人。

## M 的最短流程

1. 常驻调用 `wait_for_coordination_task(project_id)` 等待待协调 Claim；单次检查可把 `timeout_seconds` 设为 `0`。
2. 判断语义冲突、依赖、约束及是否需要人类。`active_claims` 是尚未完成的工作，`completed_claims` 是待集成工作；与后者重叠时要求后续 A 纳入其 `work_branch`，或返回 `needs_human`。
3. 调用 `submit_coordination_decision(project_id, claim_id, ...)` 提交建议。
4. C 校验建议；M 不能直接把 Claim 变为 `executing` 或 `completed`。

## 集成流程

1. 人、集成 Agent 或 CI 按团队流程把 completed `work_branch` 合入目标分支。
2. Push 目标分支；GitHub webhook 通知 C 检查 completed `work_branch` 的提交是否已经进入默认分支历史。
3. 检查通过后 C 自动转为 `integrated`，该 Claim 不再参与后续冲突协调。

普通 A 不调用 `integrate_claim`。关闭 webhook、使用其它 Git provider，或 squash/rebase 使祖先关系无法证明时，管理员核验代码后才用它兜底。

## 状态权威

Git provider 保存 Claim 和代码历史；TCA 状态数据库保存 C 的权威运行状态。A 通过 MCP 读取 Decision。MCP 是 A、M 与 C 的通信接口。只有 C 可以执行状态转换。

Claim 的运行时身份是 `(project_id, claim_id)`。不同项目可以使用相同 `claim_id`，但调用 Claim 相关 MCP tool 时必须传入 `.tca/project.yaml` 中的 `project_id`。

GitHub Claim 分支命名为 `tca/claim/<agent-id>/<claim-id>`。`agent-id` 必须等于 `.tca/claim.yaml` 的 `author`，`claim-id` 必须等于 `.tca/claim.yaml` 的 `claim_id`。首次提交前 C 会确认 `base_commit` 等于远端默认分支当前提交，并且是 Claim 分支的祖先。每个逻辑任务使用新的 `claim_id`；同一个 Claim 需要补充范围时复用同一分支并递增 `version`，但不能改变 `author`、`base_commit` 或 `work_branch`。

`agent-id` 是每个 Agent 工作区的稳定身份，从本机环境变量 `TCA_AGENT_ID` 读取。`work_branch` 固定为 `feature/<agent-id>/<claim-id>`，避免不同 Agent 的工作分支互相覆盖。

如果 MCP 不可连接、Claim 状态不明确或所需操作不在 `allowed_actions` 中，Agent 必须停止共享范围修改并向人报告。

当前协议使用 long-polling。TCA 不主动唤醒 A/M；A 使用 `wait_for_decision` 等待决策，M Runner 使用 `wait_for_coordination_task` 常驻等待协调任务。

## 凭证

公网 TCA 使用 Bearer Token。TCA Server 管理者只生成一次 `TCA_API_TOKEN`，再通过安全渠道交给获准使用该服务的团队成员。当前极简版本使用团队共享 token，不是每位成员或每个 Agent 各自生成 token。

这个 token 当前属于整个 TCA Server，不属于单个项目。`project_id` 隔离 Claim 状态，但不隔离访问权限；同一个实例只应服务同一信任范围内的项目。

仓库只提交服务地址和 token 环境变量名。成员把收到的 `TCA_API_TOKEN` 保存到 MCP 客户端的机器级环境或 secret store，一台机器通常只需配置一次；不得把真实值提交到 repo、写进 prompt 或输出到日志。

A 工作区需要自己的本地运行环境配置。可复制仓库根目录的 `.env.example` 为 `.env`：

```bash
cp .env.example .env
```

然后只填写这个工作区的稳定身份：

```text
TCA_AGENT_ID=agent-a1
```

并行 A 使用不同的 `TCA_AGENT_ID`。`.env` 必须被 `.gitignore` 忽略，不得提交。

M 不需要 `TCA_AGENT_ID`，也不复用 A 的工作区。M 使用同一个团队 token 连接 MCP；做语义判断时应自己 clone 一份被测 repo，并直接以该 repo 根目录作为 M task 的工作目录。`project_id` 和 MCP URL 从 `.tca/project.yaml` 读取。M 持续调用 `wait_for_coordination_task(project_id)`；只有 Claim 元数据时，它只能做路径级和依赖级判断。

真实用户不需要 checkout TCA Server 源码仓库。如果当前 Agent 客户端没有暴露 TCA MCP tools，说明客户端 MCP 还没有配置好；Agent 必须停止共享代码修改并向人报告。
