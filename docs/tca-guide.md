# TCA 当前协议

本文件描述新项目唯一推荐的协议。旧式共享 token 与 Claim 分支 webhook 只为已有可信部署保留兼容代码，不用于新项目。

## 角色

- A：提出技术 Claim、在获批后开发、提交工作分支。
- M：查看项目 Claim 与只读仓库上下文，给每个 A 提出建议和协调决定；不提交业务代码。
- C：TCA Server 的确定性控制器，维护状态机和权威 Decision。
- 项目创建者：有仓库 admin 权限，启动项目、委派 M、核验合并后的集成状态。

创建者与 M 可以是不同的人。M 至少要能读取仓库；A 要有写入权限。服务端用安装于目标 repo 的 Metadata-only GitHub App 查询当前权限。

## 唯一 Claim 入口

1. A 阅读需求和必要源码，声明目标、planned_paths、owned_symbols、行为契约、依赖与 work_branch。
2. A 通过 MCP submit_claim 直接提交。Claim 的 base_commit 应是工作分支所用的 Git 基线 SHA。不要创建 tca/claim 分支，不要 push .tca/claim.yaml。
3. A 用 wait_for_decision 查询权威状态。只有 allowed_actions 含 start_execution，才调用 start_execution 并修改共享代码。
4. 执行中可 report_progress、publish_handoff；扩大修改范围前修订 Claim。
5. A 在自己的 Git 分支提交并 push。远程部署从工作树运行 `tca-client complete CLAIM_ID`：先核对分支、基线、干净工作树、远程分支头及实际改动路径；对声明了 `owned_symbols` 的 Python 代码，还核对顶层函数和类方法的变化。服务端校验所报路径和符号是否属于 Claim，但元数据只读的 GitHub App 无法独立读取私有仓库 diff，因此仍信任 A 的报告。非 Python 语言、模块级语句和更细的语义约束仍须审阅。`completed` 不表示已经进入默认分支；超时进入 `expired`，不再假称完成。超时后仍可用 Git 完成凭据补报交付。
6. 团队照常合并；项目创建者核验后调用 integrate_claim。可选的签名 push webhook 只负责集成通知。

C 的状态始终是唯一权威。M 通过 wait_for_coordination_task 收取任务，通过 publish_coordination_advice 和 submit_coordination_decision 提交建议。M 不能直接绕过 C 放行代码修改。

同组后来者会使尚未执行的成员重新进入审阅，不再以 `expected_group_size` 的固定人数作为放行门槛。例如 A1 已开工、A2 已获建议但尚未开工，A3 到来时，C 不打断 A1，却会撤销 A2 的旧批准，把 A2、A3 一起交给 M 重新协调。旧建议保留在 `advice_history`，不再出现在当前 `advice`；旧任务的轮次或 Claim 修订号不能再提交决定。M 从 `wait_for_coordination_task` 获取 `coordination_round` 和 `claim.revision`，分别回填到建议的 `coordination_round` 及决定的 `expected_round`、`expected_revision`。同组有成员仍在 `assessing` 时，已批准成员也不能开工。若 A2 已开工，C 不打断它，只协调后来者。同组未收到 M 决定时，C 可在超时后放行没有已声明共享边界或依赖的 Claim；有共享边界的转人工处理，不自动放行。独立 Claim 无须人为凑组或等待。

成员可调用 `get_project_metrics` 查看基于服务端事件计算的各阶段时长中位数、Claim 修订、重新审阅次数，以及 A 主动上报的返工次数和耗时。返工时 A 用 `record_subgoal_event` 报 `rework_started`、`rework_finished`，两次用同一 `subgoal_id`；服务端时间戳为准。未上报的返工不会出现在指标中。这些是协议状态间的墙钟时间，不等于 Agent 实际编码时间；评估节省总时间时仍要与相同任务的 solo / vanilla coop 配对比较。

## 身份与威胁边界

远程登录使用用户 GitHub 账号已登记的 Ed25519 SSH 公钥。客户端签署五分钟有效、只能使用一次的 TCA 挑战；服务端验签后签发八小时 TCA 专用会话，仅保存 token 哈希。每次项目操作重新检查 GitHub 仓库权限。不要把 gh auth token 或 GitHub 私钥发给 TCA。

服务器运营方仍能读取 TCA 数据库中的 Claim、建议和事件；单靠签名认证并不提供内容端到端保密。共享仓库成员本来就可能读取彼此已 push 的代码。需要私密需求时，只把必要的协调摘要发给 TCA。Git 历史也不是保密边界。

## 历史

每版 Claim 存入 claim_revisions；Claim 提交及状态转换存入 claim_events。成员可用 list_claim_revisions 和 list_claim_events 查看。记录是数据库级追加历史，不宣称抵抗数据库管理员篡改；如需独立公证，可在外部定期导出并签名摘要。

## 部署

本机使用 tca_local 和 SQLite，只协调同一电脑的工作树。跨电脑使用 TCA Server、Postgres、GitHub App 和 TCA 会话。两者的 Claim 协议相同，仅部署和鉴权不同。完整命令见 [中文 README](../README_zh.md)。
