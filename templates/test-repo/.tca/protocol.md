# TCA 协作协议

## 角色

- **A（开发 Agent）**：提出 Claim、获批后执行代码修改，并在完成时通知 C。
- **M（协调 Agent）**：从 C 获取待协调任务，理解语义冲突并提交建议。
- **C（协议控制器）**：运行在 TCA Server 中，维护状态机、校验操作并保存 Claim 状态和协调队列。
- **P（人）**：提出目标；在目标或权限无法自动裁决时作出决定。

A 和 M 都是 TCA Server 之外的客户端，都通过 MCP 与 C 交互。

## A 的最短流程

1. 从最新目标分支创建 `tca/claim/<agent-id>/<claim-id>` 分支。
2. 在该分支提交 `.tca/claim.yaml`，说明目标、工作分支、预计路径和依赖。
3. Push Claim 分支；Git provider webhook 调用 `/webhooks/github` 通知 C。
4. 调用 `wait_for_decision(project_id, claim_id)`。只有 `allowed_actions` 包含 `start_execution` 时才能开始。
5. 调用 `start_execution(project_id, claim_id)` 后，从目标分支创建 `work_branch` 并修改代码。
6. 修改完成后提交并 push `work_branch`。
7. 范围变化时停止越界修改，并通过 Claim 分支提交新版 `.tca/claim.yaml`；完成后调用 `complete_claim(project_id, claim_id)`。

## M 的最短流程

1. 常驻调用 `wait_for_coordination_task(project_id)` 等待待协调 Claim；单次检查可把 `timeout_seconds` 设为 `0`。
2. 判断语义冲突、依赖、约束及是否需要人类。
3. 调用 `submit_coordination_decision(project_id, claim_id, ...)` 提交建议。
4. C 校验建议；M 不能直接把 Claim 变为 `executing` 或 `completed`。

## 状态权威

Git provider 保存 Claim 和代码历史；TCA 状态数据库保存 C 的权威运行状态。A 通过 MCP 读取 Decision。MCP 是 A、M 与 C 的通信接口。只有 C 可以执行状态转换。

Claim 的运行时身份是 `(project_id, claim_id)`。不同项目可以使用相同 `claim_id`，但调用 Claim 相关 MCP tool 时必须传入 `.tca/project.yaml` 中的 `project_id`。

GitHub Claim 分支命名为 `tca/claim/<agent-id>/<claim-id>`。`agent-id` 必须等于 `.tca/claim.yaml` 的 `author`，`claim-id` 必须等于 `.tca/claim.yaml` 的 `claim_id`。每个逻辑任务使用新的 `claim_id`；同一个 Claim 需要补充范围时复用同一分支并递增 `version`。

`agent-id` 是每个 Agent 工作区的稳定身份，推荐从本机环境变量 `TCA_AGENT_ID` 读取。`work_branch` 推荐使用 `feature/<agent-id>/<claim-id>`，避免不同 Agent 的工作分支互相覆盖。

如果 MCP 不可连接、Claim 状态不明确或所需操作不在 `allowed_actions` 中，Agent 必须停止共享范围修改并向人报告。

当前协议使用 long-polling。TCA 不主动唤醒 A/M；A 使用 `wait_for_decision` 等待决策，M Runner 使用 `wait_for_coordination_task` 常驻等待协调任务。

## 凭证

公网 TCA 使用 Bearer Token。仓库只保存服务地址和 Token 环境变量名；每位成员在本机安全地提供 `TCA_API_TOKEN`，不得提交凭证。

A 工作区需要自己的本地运行环境配置。可复制仓库根目录的 `.env.example` 为 `.env`：

```bash
cp .env.example .env
```

然后填入真实 token：

```text
TCA_API_TOKEN=...
TCA_AGENT_ID=agent-a1
```

`.env` 必须被 `.gitignore` 忽略，不得提交。

M 不依赖被测 repo 的 `.env.example`，也不复用 A 的工作区。M 做语义判断时应自己 clone 一份被测 repo，并直接以该 repo 根目录作为 M task 的工作目录；`project_id` 和 MCP URL 同样从 `.tca/project.yaml` 读取。M 在自己的运行环境里通过原生 MCP 连接 TCA Server，并持续调用 `wait_for_coordination_task(project_id)`。只有 Claim 元数据时，M 只能做路径级和依赖级判断。

真实用户不需要 checkout TCA Server 源码仓库。如果当前 Agent 客户端没有暴露 TCA MCP tools，说明客户端 MCP 还没有配置好；Agent 必须停止共享代码修改并向人报告。
