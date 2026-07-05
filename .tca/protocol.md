# TCA 协作协议

## 角色

- **A（开发 Agent）**：提出 Claim、执行代码修改并汇报进度。
- **M（协调 Agent）**：从 C 获取待协调任务，理解语义冲突并提交建议。
- **C（协议控制器）**：运行在 TCA Server 中，维护状态机、校验操作并保存审计记录。
- **P（人）**：提出目标；在目标或权限无法自动裁决时作出决定。

A 和 M 都是 TCA Server 之外的客户端，都通过 MCP 与 C 交互。

## A 的最短流程

1. 从最新目标分支创建 `tca/claim/<claim-id>` 分支。
2. 在该分支提交 `.tca/claim.yaml`，说明目标、工作分支、预计路径和依赖。
3. Push Claim 分支；Git remote 的 `post-receive` hook 调用 `/webhooks/git` 通知 C。
4. 调用 `wait_for_decision`。只有 `allowed_actions` 包含 `start_execution` 时才能开始。
5. 执行期间调用 `heartbeat` 和 `update_progress`。
6. 范围变化时停止越界修改并更新 Claim；完成后调用 `complete_claim`。

## M 的最短流程

1. 调用 `get_coordination_task` 获取待协调 Claim 和当前活跃 Claim。
2. 参考 `.tca/agreements/` 判断语义冲突、依赖、约束及是否需要人类。
3. 调用 `submit_coordination_decision` 提交建议。
4. C 校验建议；M 不能直接把 Claim 变为 `executing` 或 `completed`。

## 状态权威

Git 保存 Claim 和代码历史；SQLite 保存 C 的运行状态。当前本地模式由 A 通过 MCP 读取 Decision。MCP 是 A、M 与 C 的通信接口。只有 C 可以执行状态转换。

如果 MCP 不可连接、Claim 状态不明确或所需操作不在 `allowed_actions` 中，Agent 必须停止共享范围修改并向人报告。

## 凭证

仓库只保存公共连接地址和协议，不保存 Token。每位成员在自己的 Agent 配置中提供 `TCA_API_TOKEN`。
