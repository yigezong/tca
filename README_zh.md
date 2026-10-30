# Team Code Agent（TCA）

TCA 在多个开发 Agent 修改同一个 Git 仓库前协调意图、接口和执行时机。A 直接向 TCA MCP 提交 Claim，等待 C 的决定，再在各自 Git 分支开发。Git 保存代码历史；TCA 保存协调历史。

- [English README](README.md)
- [Codex 接入](docs/codex-native.md)
- [协议说明](docs/tca-guide.md)
- [GitHub App 配置](docs/github-app-setup.md)
- [多人协议验证](docs/multiplayer-validation.md)
- [流程演示视频](docs/assets/tca-process-demo.mp4)

## 一套协议，两种部署

| 部署 | 状态存储 | 身份 |
| --- | --- | --- |
| 单台电脑 | 多工作树共享本地 SQLite | 本机用户；不隔离不同人 |
| 多台电脑 | 共享 TCA Server + Postgres | GitHub 已登记的 Ed25519 SSH 公钥登录，随后使用短期 TCA 专用会话 |

两种部署都按同一顺序运行：submit_claim → wait_for_decision → start_execution → 修改、提交、push → complete_claim。不需要 Claim 分支或 Claim webhook。completed 表示工作分支待集成，不等于已经合并。

仓库管理员创建 TCA 项目，并可把唯一 M 角色委派给其他有仓库读取权限的成员。M 看 Claim 和代码、提出协调建议，不提交业务代码。有仓库写入权限的开发者可自行加入为 A。服务端在每次项目操作时重新查询仓库权限。

## 本机 Codex 接入

从 TCA 源码目录执行：

~~~bash
sh scripts/install-codex.sh /absolute/path/to/your-git-repo
~~~

脚本注册本地 MCP 并安装仓库规则。将生成的共享规则提交到业务仓库；.tca/agent-id 只保留在本地。重新打开 Codex 任务后即可正常提需求。单机最多运行一个会提交代码的 A，可在单独工作树运行一个只读 M。本机模式不能充当跨电脑服务。

## 多电脑部署

创建 GitHub App，只申请仓库 Metadata: read 权限，并仅安装到需要 TCA 的仓库。生成 App 私钥，在服务端配置：

~~~bash
TCA_AUTH_MODE=github
DATABASE_URL=postgresql://...
TCA_ALLOWED_HOSTS=tca.example.com
TCA_GITHUB_APP_ID=your-app-id
TCA_GITHUB_APP_PRIVATE_KEY=your-app-private-key-pem
~~~

运行 uv run tca-app 部署服务。App 私钥和数据库凭证只放在服务端密钥配置中。App 用于查询成员当前仓库权限，不需要 Contents 或写入权限。TCA 不接受成员个人 GitHub token 作为 MCP 凭证；特别不要把 gh auth token 的输出设为 TCA_TOKEN。

每位成员先从本仓库安装 CLI，并用自己 GitHub 账号登记的 Ed25519 SSH 公钥登录：

试用时可先设置 `TCA_HOME` 为一个新建的临时目录；CLI 的密钥、会话和本机数据库会写入该目录，不碰现有的 `~/.tca`。结束后先从 GitHub 账号移除这次添加的公钥，再删除临时目录。

~~~bash
uv tool install --python 3.12 .
tca-codex login --server-url https://tca.example.com/mcp --login 你的GitHub用户名
~~~

第一次登录会在 ~/.tca/identity 生成专用私钥。若 GitHub 尚未登记其公钥，CLI 会打印公钥；到 https://github.com/settings/keys 添加后重试。私钥不发送给 TCA；客户端签署一次性挑战。成功后，本机 ~/.tca/sessions 保存一个 8 小时有效的 TCA 专用会话，服务端只保存会话 token 的哈希。

让 Codex 启动环境能读取该 TCA 会话：

~~~bash
export TCA_TOKEN="$(tca-codex token --server-url https://tca.example.com/mcp)"
~~~

若 Codex 桌面版不继承终端环境变量，需在其启动环境配置 TCA_TOKEN，再开启新任务。它只能访问 TCA，不是 GitHub 凭证，也不是模型 API key。

有仓库 admin 权限的人启动项目：

~~~bash
tca-codex start --repo /absolute/path/to/repo --server-url https://tca.example.com/mcp
~~~

项目创建者可以把 M 委派给有仓库读取权限的成员：

~~~bash
tca-codex delegate-m --project-id OWNER/REPO --login M的GitHub用户名 --server-url https://tca.example.com/mcp
~~~

新 M 登录后，在单独的只读工作树中加入。其他有写入权限的开发者在各自电脑登录、查看可进入项目并加入：

~~~bash
tca-codex projects --server-url https://tca.example.com/mcp
tca-codex join --repo /absolute/path/to/repo --server-url https://tca.example.com/mcp
tca-codex doctor --repo /absolute/path/to/repo
~~~

M 不必是仓库所有者，且可以更换。项目成员可通过 MCP 的 list_claim_revisions、list_claim_events 查询每版 Claim 和状态转换。这些是 TCA 数据库中的追加记录，并不是不可篡改的外部公证。TCA Server 运营方可以读取数据库；不要在 Claim 里放密钥。共享仓库的成员也可能看到彼此已经 push 的代码。

**已有远程项目迁移：**先安装 GitHub App 并部署新版 Server。每位成员运行 tca-codex login、设置 TCA_TOKEN，再给 start 或 join 加 --replace-local-config --replace-mcp，让旧工作树改用 TCA 专用会话。数据库会保留已有 Claim；过去已被旧版本覆盖的修订无法恢复，迁移时只会记录当下快照。

## Git 与集成

A 在自己的工作分支开发，提交并 push 后调用 complete_claim。团队按普通 Git 流程合并。项目创建者核验后可调用 integrate_claim。签名的 GitHub push webhook 可作为兼容性的合并通知，但不是 Claim 提交渠道；标准流程不需要配置它。

旧式共享 token 和 Claim webhook 的实现暂留在代码中，以兼容已有的受信任部署；新项目不要使用旧模板流程。

## 验证边界

运行 uv sync 和 uv run pytest。测试覆盖状态机、一次性 SSH 挑战、TCA 会话、GitHub App 的 Metadata-only 权限查询、M 委派、Claim 历史及本机/远程接口。真正的 GitHub App 安装和跨电脑部署仍需在目标环境做一次联调。
