# TCA 双电脑现场案例

P1 在两个独立工作树中运行只读 M 和 A1；P2 在另一台电脑运行 A2。

## 两台电脑现场实验：上游接口未合并

示例工程就在本目录 `examples/`。它有可运行的商品目录和库存查询，但 `reserve()` 仍是没有幂等键的旧接口，`Checkout.place_order()` 尚未实现。实验让 A1 改库存预留接口，A2 写结账；A2 开始时只能看到主分支上的旧接口。

**演示前准备：**在 TCA 仓库根目录执行下列命令。把 `YOUR_GITHUB_LOGIN` 换成自己的账号，并为仓库取一个未使用过的名字：

这里的 `gh` 是 GitHub CLI；首次使用先运行 `gh auth login`。这个登录用于创建、克隆和推送 GitHub 仓库，和 TCA 专用会话是两套凭证。
如果演示 repo 已经创建并含有这份起步代码，跳过下面的复制、`git init` 和 `gh repo create`，直接从配置 M 工作树开始。

```bash
mkdir -p ../stockroom-live
rsync -a --exclude=.venv --exclude=__pycache__ examples/ ../stockroom-live/
cd ../stockroom-live
git init -b main
git add .
git commit -m "Initial stockroom prototype"
gh repo create YOUR_GITHUB_LOGIN/tca-stockroom-live --private --source=. --remote=origin --push
uv run python -m unittest discover -s tests
```

在 GitHub 给 P2 账号授予仓库写权限，将 TCA GitHub App **额外安装到这个新仓库**。P1 把当前目录作为 M clone；先在此运行 `tca-codex start --repo . --server-url https://YOUR_TCA_HOST/mcp`，再提交并推送它生成的 `AGENTS.md`、`.agents/skills/`、`.tca/project.yaml` 和 `.gitignore`。`.tca/agent-id` 只留本地。然后分别在 P1 和 P2 的电脑上准备 A 工作树：

```bash
# P1 的电脑：从 M clone 所在目录的上一级执行
gh repo clone YOUR_GITHUB_LOGIN/tca-stockroom-live stockroom-a1
tca-codex join --role a --repo ./stockroom-a1 --server-url https://YOUR_TCA_HOST/mcp
tca-codex doctor --repo ./stockroom-a1

# P2 的电脑：在自己的工作目录执行
gh repo clone YOUR_GITHUB_LOGIN/tca-stockroom-live stockroom-a2
tca-codex join --repo ./stockroom-a2 --server-url https://YOUR_TCA_HOST/mcp
tca-codex doctor --repo ./stockroom-a2
```

两人都需先完成各自的 `tca-codex login`、让 Codex 能读取各自的 `TCA_TOKEN`，并在 join 后**新开** Codex 对话。M、A1、A2 必须分别打开对应工作树。不要把 TCA 源码仓库当业务仓库做实验。

| 顺序 | 操作者 | 检查点 |
| --- | --- | --- |
| 1 | P1 的 **A1** Codex | 披露 Claim，停在待协调状态；不要先改代码。 |
| 2 | P1 的 **M** Codex | 领取 A1 任务，确认拟议接口和责任；随后让 A1 继续。 |
| 3 | A1 发布未提交工作树摘要后，P2 的 **A2** Codex | 从旧主分支提交下游 Claim；先不写代码。 |
| 4 | P1 的 **M** Codex | 查看 A1 最新摘要和 A1/A2 Claim，给 A2 明确接口和文件责任；摘要不是源码。 |
| 5 | P2 的 **A2** Codex | 获准后写独立部分；需要精确上游实现时等接口检查点。 |
| 6 | P1 的 **A1** Codex | commit/push 稳定接口检查点并 `publish_handoff`，仍不合并主分支。 |
| 7 | 两边各自完成；P1 做临时集成 | 合并两条分支、运行测试、核对功能，再决定是否合入主分支。 |

**第 1 步，发给 A1：**

> 你负责库存预留。同一个 `order_id` 的重复请求不得重复扣库存；库存不足或数量非法时不得扣库存。预计需要改变 `Inventory.reserve` 的调用方式与返回契约，保持 `available()` 的现有查询语义。先按 TCA 规则提交 Claim，声明你负责 `src/stockroom/inventory.py`、`src/stockroom/contracts.py` 和库存测试，协调组为 `stockroom-checkout`；在 Claim 中披露拟议接口和不确定点。获准后编写实现与测试，先停在**本机未提交**状态，向我报告进度，不要 commit、push 或 merge。

**第 2 步，发给 M：**

> 作为只读 M，领取 `stockroom-checkout` 的协调任务，阅读 Claim 与必要的基线代码。给 A1 明确接口责任和约束，提交当前轮次的建议与决定。不要修改业务代码，也不要把尚未提交的工作树当成你已看见的代码。

M 提交决定后，再发给 A1：“重新查询我的 Claim；若允许，调用 `start_execution`，按已批准的契约开发库存和测试。暂时停在本机未提交状态；不要 commit、push 或 merge。”A1 开工后，在 **A1 工作树的另一个终端**运行 `tca-client watch-worktree CLAIM_ID --interval 5`（用真实 Claim ID 替换）；接口行为确定后运行 `tca-client snapshot CLAIM_ID --summary "reserve 使用 order_id 幂等；失败不扣库存"`。确认服务端 Claim 的 `worktree_snapshot` 已更新，再进入第 3 步。监测终端保持运行到 Claim 结束。

**第 3 步，P2 发给 A2：**

> 你负责结账流程：实现 `Checkout.place_order(sku, quantity, order_id)`，成功时返回可核对的订单结果，失败时不能产生已支付订单或重复扣库存。上游正在改变库存预留接口，但工作尚未合入主分支。先按 TCA 规则提交 Claim，协调组为 `stockroom-checkout`；如可能需要改 `src/stockroom/contracts.py`，如实写进计划路径，并声明你需要上游预留接口。提交后等待 C/M 决定，**不要先按主分支的旧 `reserve()` 接口开工**。

**第 4 步，发给 M：**

> A2 的 Claim 已到。只读查看 A1、A2 的 Claim 和现有 Git 代码；提交建议前刷新 A1 的 Claim，检查 `worktree_snapshot` 的接口变化、行为说明和 `observed_at`。给 A2 可执行建议：谁拥有库存接口与契约、A2 只改哪些文件、结账如何处理成功/失败与幂等、哪些内容必须等接口检查点。摘要缺失或过期时说明未知，不把摘要当成可运行源码。使用本轮任务的 `coordination_round` 和 `claim.revision` 提交建议与决定；不打断 A1。

**第 5 步，P2 发给 A2：**

> 查询我的 Claim 当前状态；若允许则调用 `start_execution`。按 M 的接口与文件责任实现结账代码和测试。不要直接改 A1 拥有的库存/契约文件；若真实接口尚未出现在可获取的 Git 提交中，先完成独立部分并说明停在哪一步，不能猜测未提交代码。

**第 6 步，发给 A1：**

> 把已稳定的最小库存接口做成可获取的 Git 检查点：commit 并 push 到你的工作分支，不合并主分支。调用 `publish_handoff`，提供提交 SHA、导出符号和行为契约。其余未完成实现在你的分支继续；向 A2 提供分支与 SHA，不让对方依赖你电脑上的未提交文件。

**第 7 步，P1、P2 分别发给自己的 A：**“读取已公布的接口检查点，完成你负责的代码与测试，提交并推送自己的分支，按 TCA 报告完成。不要替对方修改其拥有的模块。”随后项目创建者在**临时集成分支**合并两条分支、运行全部测试并检查实际行为。通过后再合入主分支；失败时记录具体是接口、测试、代码还是 Git 冲突。

观察三个结果：**A2 是否在看不到未提交代码时得到准确指导；A1 是否能用未合并的接口检查点解锁 A2；两条分支合并后功能是否正确。**同时记录 Claim 到开工、检查点到下游可验证、最终集成的耗时。
