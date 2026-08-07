# TCA 被测仓库协作入口

本仓库已接入 Team Code Agent（TCA）协议。人类给你的普通需求就是产品目标；不要要求人类在 prompt 里额外指定 A/M 角色。作为开发 Agent，你在修改共享代码前必须先通过 TCA 发布 Claim 并等待 C 放行。

最小流程：

1. 阅读 `.tca/protocol.md`。
2. 读取 `.tca/project.yaml`，确认项目标识、TCA MCP 地址和 `token_env`。
3. 从本机环境或 `.env` 读取 `TCA_API_TOKEN` 和稳定的 `TCA_AGENT_ID`；`.env` 不得提交，也不要输出 `TCA_API_TOKEN` 的值。只需要读取身份时，可用 `sed -n 's/^TCA_AGENT_ID=//p' .env`。
4. 为每个人类需求生成新的 `claim_id`，建议格式为 `<TCA_AGENT_ID>-<task-slug>`。
5. 修改共享代码前，通过 Git 分支 `tca/claim/<TCA_AGENT_ID>/<claim_id>` 发布 `.tca/claim.yaml`。
6. `author` 必须等于 `TCA_AGENT_ID`，`work_branch` 建议使用 `feature/<TCA_AGENT_ID>/<claim_id>`。
7. 通过 TCA MCP 查询 Claim 状态与 `allowed_actions`；Claim 调用必须带 `project_id` 和 `claim_id`。
8. 只有 C 返回允许 `start_execution` 时才可开始修改。
9. 修改完成后提交并 push `work_branch`，再调用 `complete_claim(project_id, claim_id)`。

C 返回的状态是唯一权威状态，不得依据本地上下文自行推断。

如果当前 Agent 客户端没有暴露 TCA MCP tools，说明这个工作区还没有正确接入 TCA。此时必须停止共享代码修改并向人报告，不能假设本机存在 TCA Server 源码仓库或使用 `../proj_tca` 之类的维护者路径。

M 是外部 Agent，不在被测 repo 工作区里假扮开发者，也不复用 A 的工作区。M 做语义判断时应自己 clone 一份被测 repo，并直接以该 repo 根目录作为 M task 的工作目录；`project_id` 和 MCP URL 同样从 `.tca/project.yaml` 读取。M 通过原生 MCP 连接 TCA Server，持续调用 `wait_for_coordination_task` 领取协调任务并提交建议，不能直接修改 TCA 数据库或强制改变状态。如果 M 只有 Claim 元数据，就只能做路径级/依赖级判断。
