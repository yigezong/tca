# TCA 协作入口

先读取 .tca/project.yaml、.tca/agent-id 和 .tca/protocol.md。.tca/agent-id 是当前工作树的本地身份，不提交到 Git。

如果身份是 agent-m：只读代码和 Claim，使用 wait_for_coordination_task、publish_coordination_advice、submit_coordination_decision；不要改业务代码。

其它 agent-* 身份是 A：理解需求后通过 TCA MCP submit_claim 直接提交 Claim，等待 C 返回 start_execution 权限才修改共享代码。在自己的 Git 工作分支开发、提交并 push，最后调用 complete_claim。不要创建 Claim 分支或把 .tca/claim.yaml push 到 Git。Claim 历史由 TCA 保存，可用 list_claim_revisions 和 list_claim_events 查询。

如果 MCP 未连接或身份不匹配，先停止共享代码修改并报告。
