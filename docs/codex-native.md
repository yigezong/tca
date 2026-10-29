# 在现有 Codex 中使用 TCA

TCA 保留 Codex 原有的聊天、代码、终端、模型和登录，只增加仓库级规则及 MCP 协调工具。Claim 由 A 通过 MCP 直接提交，不需要先 push Claim 文件。

## 单机

从 TCA 源码目录执行：

~~~bash
sh scripts/install-codex.sh /absolute/path/to/your-git-repo
~~~

脚本注册 tca_local、本地 SQLite 与共享仓库规则。提交生成的 AGENTS.md、.agents/skills/tca-coordination/SKILL.md、.tca/project.yaml 和 .gitignore；不要提交 .tca/agent-id。重新打开 Codex 任务。A 与 M 要在不同工作树运行，同一台电脑最多一个会提交代码的 A。本机模式没有跨用户身份认证。

## 跨电脑

按 [中文 README](../README_zh.md) 配置 Metadata-only GitHub App、Postgres 和 TCA Server。每个人运行 tca-codex login，用 GitHub 已登记的 Ed25519 SSH 公钥签署一次性挑战。CLI 为 Codex 准备 TCA 专用短期会话，不会把个人 GitHub token 发给 TCA Server。

项目创建者用 tca-codex start 启动 repo，可用 tca-codex delegate-m 更换 M；其他有仓库写入权限的成员用 tca-codex projects 和 tca-codex join 加入。Codex 启动时需能读取 TCA_TOKEN；若桌面版没有继承终端环境变量，在应用启动环境配置后开启新任务。

## Claim 时序

A 提交 Claim、等待 Decision，只有允许 start_execution 才开始改共享代码。两份 Claim 若在开工前组成同一 coordination_group，M 同时审阅并分别提出建议；如果 A1 已进入 executing，后到的 A2 只收到针对自己的协调，A1 不被打断。A 完成并 push 工作分支后调用 complete_claim；合并仍走团队普通 Git 流程。

Claim 修订及状态转换可用 list_claim_revisions 和 list_claim_events 查询。不要在 Claim 中放秘密信息。
