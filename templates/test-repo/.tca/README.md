# 本仓库的 TCA 配置

.tca/project.yaml 保存项目 ID、MCP 地址和 TCA_TOKEN 环境变量名。.tca/agent-id 是工作树本地身份，必须被 .gitignore 忽略。AGENTS.md 和 .tca/protocol.md 给 Codex 提供协作规则。

新项目推荐运行 tca-codex start 或 tca-codex join 自动配置。A 通过 MCP 直接提交 Claim；不需要 Claim 分支或 Claim webhook。完成后的代码仍按普通 Git 流程合并。参见 TCA 的 README_zh.md。
