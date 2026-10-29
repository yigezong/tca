# TCA 项目协作入口

本仓库是 Team Code Agent（TCA）服务本体。

本仓库按普通 Git 开发流程维护，不使用 TCA 自举管理自身。修改本仓库时，先理解代码与文档，保持改动最小，测试通过后再提交。

本仓库根目录不保留 `.tca/` 协议配置或 `.codex/` MCP 活配置；这些文件属于被测 repo。被测 repo 接入模板保存在 `templates/test-repo/`。

TCA 协议优先用于独立的被测 repo。配置被测 repo 或在被测 repo 中让 Agent 工作时，必须遵守：

1. 阅读被测 repo 的 `.tca/protocol.md` 和 `.tca/project.yaml`。
2. 修改共享代码前，通过 TCA MCP 的 `submit_claim` 直接申报 Claim，不创建 Claim 分支。
3. 通过 TCA MCP 查询 Claim 状态与 `allowed_actions`。
5. 只有 C 返回允许 `start_execution` 时才可开始修改。

完成后调用 `complete_claim`。C 返回的状态是唯一权威状态，不得依据本地上下文自行推断。

M 是外部 Agent。M 只能通过 MCP 领取协调任务并提交建议，不能直接修改 TCA 数据库或强制改变状态。
