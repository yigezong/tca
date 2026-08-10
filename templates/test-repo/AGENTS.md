# TCA 被测仓库协作入口

本仓库已接入 Team Code Agent（TCA）协议。人类给你的普通需求就是产品目标；不要要求人类在 prompt 里额外指定 A/M 角色。作为开发 Agent，你在修改共享代码前必须先通过 TCA 发布 Claim 并等待 C 放行。

最小流程：

1. 阅读 `.tca/protocol.md`。
2. 读取 `.tca/project.yaml`，确认项目标识、TCA MCP 地址和 `token_env`。
3. 从工作区 `.env` 读取稳定的 `TCA_AGENT_ID`。团队共享的 `TCA_API_TOKEN` 应已由 MCP 客户端从机器级环境或 secret store 读取；不要自行生成、要求人类粘贴或输出它。只读取身份时，可用 `sed -n 's/^TCA_AGENT_ID=//p' .env`。
4. 为每个人类需求生成新的 `claim_id`，建议格式为 `<TCA_AGENT_ID>-<task-slug>`。
5. 发布 Claim 前先 `git fetch origin`；`base_commit` 必须是此刻远端默认分支的完整 commit SHA，Claim 分支必须从该提交创建。
6. 通过 Git 分支 `tca/claim/<TCA_AGENT_ID>/<claim_id>` 发布 `.tca/claim.yaml`。`author` 必须等于 `TCA_AGENT_ID`，`work_branch` 必须是 `feature/<TCA_AGENT_ID>/<claim_id>`，新 Claim 的 `version` 必须是 `1`。
7. 通过 TCA MCP 查询 Claim 状态与 `allowed_actions`；Claim 调用必须带 `project_id` 和 `claim_id`。
8. 只有 C 返回允许 `start_execution` 时才可开始修改。
9. 从 Claim 声明的 `base_commit` 创建 `work_branch`，执行 Decision 约束后再修改代码。完成后提交并 push，最后调用 `complete_claim(project_id, claim_id)`。

`completed` 表示工作分支待集成，不等于已进入目标分支。如果 Decision 要求纳入另一个 completed Claim，先获取并检查它声明的 `work_branch`，把必要工作纳入自己的基线后再编辑；无法安全处理时停止并报告。集成者只走团队正常 Git 流程；GitHub 默认分支 push 后由 TCA 自动确认 `integrated`，普通 A 不调用 `integrate_claim`。

C 返回的状态是唯一权威状态，不得依据本地上下文自行推断。

如果当前 Agent 客户端没有暴露 TCA MCP tools，说明这台机器的 MCP 地址或共享 token 还没有正确配置。此时必须停止共享代码修改并向人报告，不能自行生成 token，也不能假设本机存在 TCA Server 源码仓库或使用 `../proj_tca` 之类的维护者路径。
