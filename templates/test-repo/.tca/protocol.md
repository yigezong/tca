# TCA 协议

A 和 M 经同一个 MCP 服务与 C 通信。A 通过 submit_claim 直接申报目标、修改范围、接口、依赖和 Git 基线；wait_for_decision 返回权威状态。只有 allowed_actions 包含 start_execution 时才能开工。A 在自己的 Git 工作分支完成并 push 后调用 complete_claim。团队照常合并；项目创建者核验后调用 integrate_claim。

M 从 wait_for_coordination_task 获取任务，结合 Claim 和只读代码上下文，向相关 A 发布建议并提交协调决定。A1 已在执行时，后到的 A2 只获得针对自己的建议，M 不打断 A1。

Claim 不需要 Git 分支、claim.yaml 或 webhook。Git 用于代码与合并历史；TCA 数据库记录每版 Claim 和状态转换。不得在 Claim 放密钥。远程登录使用 GitHub 已登记的 SSH 公钥及 TCA 专用短期会话，不向 TCA 发送个人 GitHub token。

详细说明见 TCA 源码仓库的 README_zh.md 和 docs/tca-guide.md。
