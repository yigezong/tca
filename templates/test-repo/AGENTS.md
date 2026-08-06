# TCA 被测仓库协作入口

本仓库已接入 Team Code Agent（TCA）协议。Agent 修改共享代码前，必须先通过 TCA 发布 Claim 并等待 C 放行。

最小流程：

1. 阅读 `.tca/protocol.md`。
2. 读取 `.tca/project.yaml`，确认项目标识、TCA MCP 地址和 `token_env`。
3. 在每个 A/M 运行环境里提供 `TCA_API_TOKEN`；可复制 `.env.example` 为 `.env`，但不得提交真实 `.env`。
4. 修改共享代码前，通过 Git 分支发布 `.tca/claim.yaml`。
5. 通过 TCA MCP 查询 Claim 状态与 `allowed_actions`；Claim 调用必须带 `project_id` 和 `claim_id`。
6. 只有 C 返回允许 `start_execution` 时才可开始修改。

完成后调用 `complete_claim(project_id, claim_id)`。C 返回的状态是唯一权威状态，不得依据本地上下文自行推断。

M 是外部 Agent。M 只能通过 MCP 领取协调任务并提交建议，不能直接修改 TCA 数据库或强制改变状态。
