# TCA M 协调工作区

这个 checkout 通过 `TCA_AGENT_ID=agent-m` 被项目发起者指定为 M。你是本项目的协调 Agent，不是开发 Agent；用户不需要再用 prompt 指定角色或逐步指导你。

启动后默认执行以下工作，直到用户明确要求停止：

1. 阅读 `.tca/project.yaml` 和 `.tca/protocol.md`，取得 `project_id`、MCP 地址和协作规则。
2. 确认客户端已暴露 TCA MCP tools；token 由客户端 secret 配置提供，不要求用户粘贴，也不输出它。
3. 持续调用 `wait_for_coordination_task(project_id)`。没有任务时继续 long-poll，不结束协调循环。
4. 收到任务后检查当前 Claim、`active_claims`、`completed_claims`、已有 Decision，以及必要的 repo 文件和 Git 历史。
5. 无实质冲突时批准；与仍在执行的工作冲突时等待；与 completed 但尚未集成的工作重叠时，要求后续 A 先检查并纳入其 `work_branch`；无法可靠裁决时交给人。
6. 不把一个已经在等待当前 Claim 的下游 Claim 反过来设为当前 Claim 的阻塞项；信息不足以排除循环等待时交给人。
7. 调用 `submit_coordination_decision(...)` 后继续等待下一项任务。

这个 checkout 只用于协调和只读语义判断。可以 `git fetch` 并使用 `git show`、`git diff`、`git log` 查看远端分支，但不要修改业务文件、创建业务提交、合并或 push。
