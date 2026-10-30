# 多人协议验证：先隔离模拟，再真实联调

## 第一层：在一台电脑上验证协议实现

从 TCA 源码目录运行：

```bash
PYTHONDONTWRITEBYTECODE=1 uv run --no-sync pytest -p no:cacheprovider -v tests/test_multiplayer_protocol.py
```

测试使用临时 SQLite 数据库、五个不同的临时 Ed25519 密钥和 GitHub 权限替身，不读取现有 GitHub、Codex 或 TCA 凭证，不访问外网，也不改 Codex 配置。它通过真实 HTTP 路由和 MCP 工具验证：

1. 别人的公钥不能冒充项目创建者；失败或成功用过的一次性挑战不能重放。
2. 个人 GitHub token 和无效 TCA token 被拒绝；不同登录得到不同 TCA 会话，数据库只存会话哈希。
3. 只有仓库管理员能启动项目、委派 M；只读 M 能协调，但不能提交 A 的 Claim；A 不能冒用另一个 A。
4. A1 执行中，同一协调组的 A2 后到时仍可并行；M 只领到 A2 的新任务，A1 保持 executing；A 无权领取协调任务。
5. 未授权用户看不到 Claim 历史；撤销仓库权限后，既有会话不能再操作该项目；会话过期后被拒绝。

这是应用层端到端测试。GitHub API 和网络传输由本机替身代替，因此**不能据此宣称真实跨电脑部署已通过**。

## 第二层：真实跨电脑联调的准备

使用一个不含真实业务代码的临时私有仓库，四个不同的 GitHub 账号分别担任项目创建者、M、A1、A2。创建者有仓库 admin；M 至少有 read；A1、A2 有 write。GitHub App 只安装到这个仓库，仓库权限仅设 Metadata: read，见 [配置说明](github-app-setup.md)。

另起一个测试用 Postgres 和 HTTPS TCA 服务，不复用生产数据库、现有 TCA 服务或真实项目。服务端只配置 App ID 和 App 私钥；个人 GitHub token 不进入服务端。HTTPS 入口必须屏蔽 Authorization 请求头的访问日志。可以在测试结束后销毁服务、数据库和仓库。

各参与者先在自己的电脑上设置临时客户端目录，然后登录：

```bash
export TCA_HOME="$(mktemp -d "${TMPDIR:-/tmp}/tca-live.XXXXXXXX")"
export TCA_MCP_URL="https://YOUR-TEST-HOST/mcp"
uv run tca-codex login --server-url "$TCA_MCP_URL" --login YOUR_GITHUB_LOGIN
export TCA_TOKEN="$(uv run tca-codex token --server-url "$TCA_MCP_URL")"
```

首次登录会输出需要添加到自己 GitHub 账号的**公钥**。添加后重新运行登录命令。不要在聊天、日志或截图里展示私钥、TCA_TOKEN、App 私钥或完整 Authorization 头。`TCA_HOME` 隔离了本机密钥、会话和数据库；这一阶段不运行 `join`，因此也不改现有 Codex MCP 配置。

项目创建者依次运行：

```bash
uv run tca-codex start --project-id OWNER/TEST_REPO --server-url "$TCA_MCP_URL"
uv run tca-codex delegate-m --project-id OWNER/TEST_REPO --login M_GITHUB_LOGIN --server-url "$TCA_MCP_URL"
```

M、A1、A2 分别运行：

```bash
uv run tca-codex projects --server-url "$TCA_MCP_URL"
```

预期 M 只看到自己的 M 身份，A1/A2 分别只看到 A 身份。再使用各自的 TCA 会话，通过 MCP 提交/协调两个属于同一协调组的 Claim：A1 先提交并开始执行；A2 后提交一份有重叠路径的 Claim；M 只领取 A2 的新任务并给 A2 建议。确认 A1 未被打断、A2 获准并行，A1/A2 均不能用对方身份提交。之后撤销 A2 的仓库写权限，确认 A2 的已有 TCA 会话不能继续操作该项目；撤销测试公钥只阻止**新登录**，已有会话在八小时内仍可用于身份识别，但仓库权限会重新检查。

## 必须报告的结果与边界

记录各步 HTTP 状态码、M/A 所见角色、Claim 状态和服务器是否记录原始凭证。不要记录 token 值。真实联调至少还要确认：GitHub App 确实仅有 Metadata: read、私钥未发到客户端、HTTPS 有效、服务器未记录 Authorization 头、Claim 数据只对有权限的项目成员开放。

TCA Server 仍能看到和保存 Claim、协调建议和短期 TCA 会话的请求。它不能凭这些会话获得用户的 GitHub 个人 token 或推送代码，但**不可信的服务端仍可泄露或篡改协调内容**。要抵抗恶意服务运营方，需要进一步设计客户端验证的端到端签名；当前实现并不提供这个保证。共享仓库成员也可能通过 Git 看见彼此已经 push 的代码。

测试结束后，在 GitHub 账号移除本次临时公钥，卸载测试仓库上的 App，销毁测试服务、数据库和仓库，再删除各电脑的 `TCA_HOME` 临时目录。
