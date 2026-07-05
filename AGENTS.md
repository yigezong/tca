# TCA 项目协作入口

本仓库使用 Team Code Agent（TCA）协议协调多个开发 Agent。

开始任务前必须：

1. 阅读 `.tca/protocol.md`。
2. 读取 `.tca/project.yaml`，确认项目标识、Claim 分支和 TCA MCP 地址。
3. 在修改共享代码前，通过 Git 分支发布 `.tca/claim.yaml`。
4. 通过 TCA MCP 查询 Claim 状态与 `allowed_actions`。
5. 只有 C 返回允许 `start_execution` 时才可开始修改。

执行期间应通过 MCP 汇报心跳和进度；完成后调用 `complete_claim`。C 返回的状态是唯一权威状态，不得依据本地上下文自行推断。

M 也是外部 Agent。M 只能通过 MCP 领取协调任务并提交建议，不能直接修改 TCA 数据库或强制改变状态。
