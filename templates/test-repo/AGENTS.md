# TCA 被测仓库协作入口

本仓库已接入 Team Code Agent（TCA）协议。启动时先用 `sed -n 's/^TCA_AGENT_ID=//p' .env` 读取工作区身份，不要要求人类在 prompt 里指定 A/M 角色：

- 值为 `agent-m`：这个工作区是 M。立即阅读并只遵循 `.tca/m-agent.md`，不要执行下面的 A 流程。
- 其它非空值：这个工作区是 A。人类给你的普通需求就是产品目标，按下面流程工作。
- 缺失或为空：停止并报告工作区尚未设置 `TCA_AGENT_ID`。

作为 A，你在修改共享代码前必须先通过 TCA 发布 Claim。TCA 默认不会让
独立工作等待 M 或等所有同伴到齐：只有你明确声明了 `after_interface`
依赖时，才等待相关 A 发布稳定接口。

最小流程：

1. 阅读 `.tca/protocol.md`。
2. 读取 `.tca/project.yaml`，确认项目标识、TCA MCP 地址和 `token_env`。
3. 使用已读取的稳定 `TCA_AGENT_ID`。团队共享的 `TCA_API_TOKEN` 应已由 MCP 客户端从机器级环境或 secret store 读取；不要自行生成、要求人类粘贴或输出它。
4. 人类/P 只提供分配给你的自然语言 feature 需求；不要假定 P 已经给出文件、函数、接口或实现边界。为该 feature 生成新的 `claim_id`，建议格式为 `<TCA_AGENT_ID>-<task-slug>`。
5. 在发布 Claim 前，先自行阅读自己 feature 所需的普通项目源码，并由你自己推断、声明技术责任边界：`planned_paths`、`owned_symbols`、行为契约、临时验证。它们是 A 对 TCA 的可修订披露，不是 P/M 下达的实现方案。
6. 发布 Claim 前先 `git fetch origin`；`base_commit` 必须是此刻远端默认分支的完整 commit SHA，Claim 分支必须从该提交创建。
7. 通过 Git 分支 `tca/claim/<TCA_AGENT_ID>/<claim_id>` 发布 `.tca/claim.yaml`。`author` 必须等于 `TCA_AGENT_ID`，`work_branch` 必须是 `feature/<TCA_AGENT_ID>/<claim_id>`，新 Claim 的 `version` 必须是 `1`。
8. 通过 TCA MCP 查询 Claim 状态与 `allowed_actions`；Claim 调用必须带 `project_id` 和 `claim_id`。
9. 只有 C 返回允许 `start_execution` 时才可开始修改。普通无依赖 Claim
   会被 C 立即批准；`planned_paths` 重叠只会附带整合约束，不会自动阻塞。
10. 从 Claim 声明的 `base_commit` 创建 `work_branch`，执行 Decision 约束后再修改代码。你自己拆分 subgoal，并仅在开始、结束或需要整合时调用 `record_subgoal_event`；完成后提交并 push，最后调用 `complete_claim(project_id, claim_id)`。

接口先行协作：人类给的是高层产品目标；你要在 Claim 中自己写清技术边界（`planned_paths`、`owned_symbols`、行为契约、临时验证），不要把其它 A 的实现细节塞进 prompt。若只依赖另一位 A 的稳定接口，Claim 使用 `dependency_mode: after_interface` 和 `handoff_from`，等待该 A 的 `publish_handoff`，收到放行约束后 rebase 到 handoff commit 并保持其中的行为契约；不要等对方无关工作全部完成。你在执行中可调用 `publish_handoff` 发布自己的 commit、导出符号和契约。若你是后提交者，必须基于最新相关提交自行解决 rebase、接口适配和 Git conflict；这类技术问题绝不能升级给 P。

`needs_human` 只用于产品需求本身没有唯一含义时。此时调用
`request_requirements_clarification`，只提交面向自己 P 的自然语言问题及
最小必要上下文；不要附带 patch、完整 diff 或其它 A 的私有工作内容。

`completed` 表示工作分支待集成，不等于已进入目标分支。如果 Decision 要求纳入另一个 completed Claim，先获取并检查它声明的 `work_branch`，把必要工作纳入自己的基线后再编辑；无法安全处理时停止并报告。集成者只走团队正常 Git 流程；GitHub 默认分支 push 后由 TCA 自动确认 `integrated`，普通 A 不调用 `integrate_claim`。

C 返回的状态是唯一权威状态，不得依据本地上下文自行推断。

如果当前 Agent 客户端没有暴露 TCA MCP tools，说明这台机器的 MCP 地址或共享 token 还没有正确配置。此时必须停止共享代码修改并向人报告，不能自行生成 token，也不能假设本机存在 TCA Server 源码仓库或使用 `../proj_tca` 之类的维护者路径。
