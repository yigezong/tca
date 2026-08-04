# TCA 协作协议

## 角色

- **A（开发 Agent）**：提出 Claim、获批后执行代码修改，并在完成时通知 C。
- **M（协调 Agent）**：从 C 获取待协调任务，理解语义冲突并提交建议。
- **C（协议控制器）**：运行在 TCA Server 中，维护状态机、校验操作并保存 Claim 状态和协调队列。
- **P（人）**：提出目标；在目标或权限无法自动裁决时作出决定。

A 和 M 都是 TCA Server 之外的客户端，都通过 MCP 与 C 交互。

## A 的最短流程

1. 从最新目标分支创建 `tca/claim/<claim-id>` 分支。
2. 在该分支提交 `.tca/claim.yaml`，说明目标、工作分支、预计路径和依赖。
3. Push Claim 分支；Git provider webhook 调用 `/webhooks/github` 通知 C。
4. 调用 `wait_for_decision`。只有 `allowed_actions` 包含 `start_execution` 时才能开始。
5. 范围变化时停止越界修改，并通过 Claim 分支提交新版 `.tca/claim.yaml`；完成后调用 `complete_claim`。

## M 的最短流程

1. 调用 `get_coordination_task` 获取待协调 Claim 和当前活跃 Claim。
2. 判断语义冲突、依赖、约束及是否需要人类。
3. 调用 `submit_coordination_decision` 提交建议。
4. C 校验建议；M 不能直接把 Claim 变为 `executing` 或 `completed`。

## 状态权威

Git provider 保存 Claim 和代码历史；TCA 状态数据库保存 C 的权威运行状态。A 通过 MCP 读取 Decision。MCP 是 A、M 与 C 的通信接口。只有 C 可以执行状态转换。

如果 MCP 不可连接、Claim 状态不明确或所需操作不在 `allowed_actions` 中，Agent 必须停止共享范围修改并向人报告。

## 凭证

公网 TCA 使用 Bearer Token。仓库只保存服务地址和 Token 环境变量名；每位成员在本机安全地提供 `TCA_API_TOKEN`，不得提交凭证。
