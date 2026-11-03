# TCA：在代码尚未合并时协调多人开发

[English](README.md) · [演示视频](docs/assets/tca-process-demo.mp4)

## 为什么需要它

上游开发者正在改库存模块，甚至准备改变 `reserve()` 接口。代码还在他的电脑里，没有 commit，也没有合入主分支。下游开发者只看得到旧接口，却必须开始写结账模块。直接并行容易写出两套不兼容的假设；一直等上游合并，又会浪费可并行的时间。

TCA 让开发者**先披露意图，再修改共享代码**。上游即使暂时没有可见代码，也要把预计修改范围、接口和不确定点写成 Claim；协调者据此约定责任。下游需要精确代码时，上游可先在自己的分支提交稳定的**接口检查点**，无需等整个功能完成或合并主分支。

**边界：**TCA 不能读取另一台电脑上尚未 commit 的工作树，也不能凭 Claim 还原真实实现。Claim 解决“知道对方打算做什么”；分支上的接口检查点解决“拿到可运行的具体代码”。接口未定时应明确等待检查点，不能假称已看见最新代码。

## 角色与 harness

这里的 **harness** 是包在 Codex 等编码 Agent 外面的执行与协调规则：何时披露 Claim、什么状态可以开工、M 看哪些上下文、怎样交接接口、怎样报告完成。原来各 Agent 只看自己的 Git 快照；TCA 增加一份共享协调状态。

| 角色 | 职责 |
| --- | --- |
| **A：开发者** | 提交 Claim，获准后用自己的 Codex 和 Git 分支写代码；一台电脑最多一个会提交代码的 A。 |
| **M：协调者** | 只读 Claim 和必要源码，为各 A 明确责任、接口、依赖和验收建议；不提交业务代码，也看不到未提交的代码。 |
| **C：控制器** | 保存权威状态，检查规则和权限，处理 M 的决定，拒绝过期协调结果；不代替代码审查和 Git 合并。 |
| **项目创建者** | 用有仓库管理权限的账号启动项目、委派 M、核验最终集成；不必亲自担任 M。 |

有写权限的项目创建者可以在**两个独立工作树**中分别担任一个只读 M 和一个 A。因此两个人、两台电脑即可做实验：你在自己的电脑运行 M 和 A1，导师在另一台电脑运行 A2。每个人仍只用自己的 GitHub 账号提交自己的代码。

## 协议与状态

1. A 提交 **Claim**：目标、预计路径与符号、行为契约、依赖、Git 基线及工作分支。Claim 通过 TCA MCP 直接发送，不需要 Claim 分支或 webhook。
2. C 检查已声明边界。需要协调时，M 领取任务，分别向有关 A 提建议并提交决定。A 查询 `wait_for_decision` 或 `get_claim_state`；只有 `allowed_actions` 包含 `start_execution`，才能调用它开始修改共享代码。
3. A 在自己的分支开发，可报告进度。需要让下游提前使用稳定接口时，先 commit/push 接口检查点，再用 `publish_handoff` 声明 SHA、导出符号和行为契约；这不表示整个上游功能已完成。
4. A 完成后提交并 push，远程模式在工作树运行 `tca-client complete CLAIM_ID`。客户端核查分支、基线、远程提交、改动路径，以及已声明的 Python 顶层函数和类方法；服务端 Metadata-only GitHub App 无法独立读取私有仓库 diff，因此仍须审阅代码与 A 的报告。团队照常合并，项目创建者核验后确认 `integrated`。

| 状态 | 含义 |
| --- | --- |
| `assessing` | 正在审查或重新协调；不能开工。 |
| `approved` / `approved_with_constraints` | 获准；后者须遵守约束。A 仍须调用 `start_execution`。 |
| `waiting` | 等待接口交接或其他已声明依赖。 |
| `executing` | A 已开工，可报告进度、发布接口检查点。 |
| `needs_human` / `blocked` | 分别表示需要人澄清需求、当前方案不能放行。 |
| `expired` | 执行超时，尚无已验证交付；可补交 Git 完成凭据。 |
| `completed` / `integrated` | A 已报告交付 / 项目创建者已确认集成。**完成不等于合并。** |

### 冲突如何消解

- **开工前：**M 根据 Claim 分清共享文件、函数、数据格式与责任。必要时让一人拥有共享接口，另一人按接口开发；有接口依赖时可等检查点，不必等完整功能合并。
- **错峰到来：**A1 已开工、A2 已获建议但未开工、A3 又加入同组时，C 不打断 A1，却撤销 A2 旧批准，让 A2 与 A3 重新协调。旧建议留在历史中，不再作为当前建议；旧轮次或旧修订号的提交会被拒绝。若 A2 也已开工，M 只协调后来者。
- **交付后：**各 A 仍负责必要的 Git rebase/merge 与代码验证。TCA 减少盲目冲突，不保证所有修改都可机械合并，也不掩盖测试失败。M 超时且存在已声明共享边界时，C 不会无人审查就自动放行。

## 安装与接入

### C 已在线上：不同电脑共同使用

服务端使用共享数据库（推荐 Postgres）、HTTPS，以及只对目标仓库申请 **Metadata: read** 的 GitHub App。仓库管理员将 App 安装到目标 repo；它用于查询成员权限，不需要 Contents 或写权限。每个人自己安装 Codex 和 TCA CLI：

```bash
git clone https://github.com/yigezong/tca.git
cd tca
uv tool install --python 3.12 --force .
tca-codex login --server-url https://YOUR_TCA_HOST/mcp --login YOUR_GITHUB_LOGIN
```

首次登录会生成本机专用 Ed25519 密钥。若 CLI 提示公钥未登记，把**公钥**加到该 GitHub 账号的 SSH keys 页面，再重试。私钥不发给 TCA。登录后客户端保存约八小时有效的 TCA 专用会话，服务端只存其哈希。让启动 Codex 的环境读取会话：

```bash
export TCA_TOKEN="$(tca-codex token --server-url https://YOUR_TCA_HOST/mcp)"
```

不要把 `TCA_TOKEN`、GitHub token 或私钥放进聊天、仓库和截图。Codex 桌面版若不继承终端变量，应在其启动环境配置 `TCA_TOKEN` 后开启新任务。两人实验须在在线 C 部署包含“项目创建者同时可作 M 和 A”功能的版本，并在两台电脑更新 CLI。

项目创建者先在 M 工作树运行 `tca-codex start --repo PATH --server-url https://YOUR_TCA_HOST/mcp`，将生成的 `AGENTS.md`、`.agents/skills/`、`.tca/project.yaml`、`.gitignore` 提交推送；`.tca/agent-id` 只留本地。随后本人在另一工作树运行 `tca-codex join --role a --repo PATH --server-url https://YOUR_TCA_HOST/mcp`；另一位开发者运行 `tca-codex join --repo PATH --server-url https://YOUR_TCA_HOST/mcp`。各工作树运行 `tca-codex doctor --repo PATH`。M 也可由创建者用 `tca-codex delegate-m` 委派给其他有读取权限的成员。

### C 不部署云端

- **同一电脑试用：**从 TCA 源码目录运行 `sh scripts/install-codex.sh /absolute/path/to/repo`。本机 `tca_local` 与 SQLite 协调同机工作树；它不隔离不同人的身份，也不能让两台电脑共享状态。
- **两台电脑、C 放在其中一台：**C 仍须作为双方可访问的服务运行，配共享数据库、HTTPS 地址和相同的身份校验；可用受控内网或隧道提供 HTTPS。只在一台电脑启动 `tca_local`，另一台无法参加同一个实验。

## 两台电脑现场实验：上游接口未合并

示例工程在 [`examples/stockroom-starter`](examples/stockroom-starter)。它有可运行的商品目录和库存查询，但 `reserve()` 仍是没有幂等键的旧接口，`Checkout.place_order()` 尚未实现。实验让 A1 改库存预留接口，A2 写结账；A2 开始时只能看到主分支上的旧接口。

**演示前准备：**在 TCA 仓库根目录执行下列命令。把 `YOUR_GITHUB_LOGIN` 换成自己的账号，并为仓库取一个未使用过的名字：

这里的 `gh` 是 GitHub CLI；首次使用先运行 `gh auth login`。这个登录用于创建、克隆和推送 GitHub 仓库，和 TCA 专用会话是两套凭证。
如果演示 repo 已经创建并含有这份起步代码，跳过下面的复制、`git init` 和 `gh repo create`，直接从配置 M 工作树开始。

```bash
mkdir -p ../stockroom-live
rsync -a --exclude=.venv --exclude=__pycache__ examples/stockroom-starter/ ../stockroom-live/
cd ../stockroom-live
git init -b main
git add .
git commit -m "Initial stockroom prototype"
gh repo create YOUR_GITHUB_LOGIN/tca-stockroom-live --private --source=. --remote=origin --push
uv run python -m unittest discover -s tests
```

在 GitHub 给导师账号授予仓库写权限，将 TCA GitHub App **额外安装到这个新仓库**。你把当前目录作为 M clone；先在此运行 `tca-codex start --repo . --server-url https://YOUR_TCA_HOST/mcp`，再提交并推送它生成的 `AGENTS.md`、`.agents/skills/`、`.tca/project.yaml` 和 `.gitignore`。`.tca/agent-id` 只留本地。然后分别在你和导师的电脑上准备 A 工作树：

```bash
# 你的电脑：从 M clone 所在目录的上一级执行
gh repo clone YOUR_GITHUB_LOGIN/tca-stockroom-live stockroom-a1
tca-codex join --role a --repo ./stockroom-a1 --server-url https://YOUR_TCA_HOST/mcp
tca-codex doctor --repo ./stockroom-a1

# 导师电脑：在自己的工作目录执行
gh repo clone YOUR_GITHUB_LOGIN/tca-stockroom-live stockroom-a2
tca-codex join --repo ./stockroom-a2 --server-url https://YOUR_TCA_HOST/mcp
tca-codex doctor --repo ./stockroom-a2
```

两人都需先完成各自的 `tca-codex login`、让 Codex 能读取各自的 `TCA_TOKEN`，并在 join 后**新开** Codex 对话。M、A1、A2 必须分别打开对应工作树。不要把 TCA 源码仓库当业务仓库做实验。

| 顺序 | 操作者 | 检查点 |
| --- | --- | --- |
| 1 | 你的 **A1** Codex | 披露 Claim，停在待协调状态；不要先改代码。 |
| 2 | 你的 **M** Codex | 领取 A1 任务，确认拟议接口和责任；随后让 A1 继续。 |
| 3 | A1 报告本地未提交改动后，导师的 **A2** Codex | 从旧主分支提交下游 Claim；先不写代码。 |
| 4 | 你的 **M** Codex | 根据 A1/A2 Claim 给 A2 明确接口和文件责任，不假称看到了 A1 未提交代码。 |
| 5 | 导师的 **A2** Codex | 获准后写独立部分；需要精确上游实现时等接口检查点。 |
| 6 | 你的 **A1** Codex | commit/push 稳定接口检查点并 `publish_handoff`，仍不合并主分支。 |
| 7 | 两边各自完成；你做临时集成 | 合并两条分支、运行测试、核对功能，再决定是否合入主分支。 |

**第 1 步，发给 A1：**

> 你负责库存预留。同一个 `order_id` 的重复请求不得重复扣库存；库存不足或数量非法时不得扣库存。预计需要改变 `Inventory.reserve` 的调用方式与返回契约，保持 `available()` 的现有查询语义。先按 TCA 规则提交 Claim，声明你负责 `src/stockroom/inventory.py`、`src/stockroom/contracts.py` 和库存测试，协调组为 `stockroom-checkout`；在 Claim 中披露拟议接口和不确定点。获准后编写实现与测试，先停在**本机未提交**状态，向我报告进度，不要 commit、push 或 merge。

**第 2 步，发给 M：**

> 作为只读 M，领取 `stockroom-checkout` 的协调任务，阅读 Claim 与必要的基线代码。给 A1 明确接口责任和约束，提交当前轮次的建议与决定。不要修改业务代码，也不要把尚未提交的工作树当成你已看见的代码。

M 提交决定后，再发给 A1：“重新查询我的 Claim；若允许，调用 `start_execution`，按已批准的契约开发库存和测试。暂时停在本机未提交状态，报告已改文件；不要 commit、push 或 merge。”确认 A1 已报告本机改动，才进入第 3 步。

**第 3 步，导师发给 A2：**

> 你负责结账流程：实现 `Checkout.place_order(sku, quantity, order_id)`，成功时返回可核对的订单结果，失败时不能产生已支付订单或重复扣库存。上游正在改变库存预留接口，但工作尚未合入主分支。先按 TCA 规则提交 Claim，协调组为 `stockroom-checkout`；如可能需要改 `src/stockroom/contracts.py`，如实写进计划路径，并声明你需要上游预留接口。提交后等待 C/M 决定，**不要先按主分支的旧 `reserve()` 接口开工**。

**第 4 步，发给 M：**

> A2 的 Claim 已到。只读查看 A1、A2 的 Claim 和现有 Git 代码，确认 A1 正在开发且代码未合入主分支。给 A2 可执行建议：谁拥有库存接口与契约、A2 只改哪些文件、结账如何处理成功/失败与幂等、哪些内容必须等接口检查点。使用本轮任务的 `coordination_round` 和 `claim.revision` 提交建议与决定；不打断 A1，不声称看到了 A1 未提交的实现。

**第 5 步，导师发给 A2：**

> 查询我的 Claim 当前状态；若允许则调用 `start_execution`。按 M 的接口与文件责任实现结账代码和测试。不要直接改 A1 拥有的库存/契约文件；若真实接口尚未出现在可获取的 Git 提交中，先完成独立部分并说明停在哪一步，不能猜测未提交代码。

**第 6 步，发给 A1：**

> 把已稳定的最小库存接口做成可获取的 Git 检查点：commit 并 push 到你的工作分支，不合并主分支。调用 `publish_handoff`，提供提交 SHA、导出符号和行为契约。其余未完成实现在你的分支继续；向 A2 提供分支与 SHA，不让对方依赖你电脑上的未提交文件。

**第 7 步，两边分别发给自己的 A：**“读取已公布的接口检查点，完成你负责的代码与测试，提交并推送自己的分支，按 TCA 报告完成。不要替对方修改其拥有的模块。”随后项目创建者在**临时集成分支**合并两条分支、运行全部测试并检查实际行为。通过后再合入主分支；失败时记录具体是接口、测试、代码还是 Git 冲突。

观察三个结果：**A2 是否在看不到未提交代码时得到准确指导；A1 是否能用未合并的接口检查点解锁 A2；两条分支合并后功能是否正确。**同时记录 Claim 到开工、检查点到下游可验证、最终集成的耗时。
