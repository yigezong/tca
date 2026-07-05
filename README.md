# Team Code Agent

Team Code Agent（TCA）是一个 Git-first 的多 Agent 协作控制服务。Git 用来发布 Claim，MCP 用来让 A、M 与 C 交互，C 是唯一的状态权威。

## 当前结构

```text
A（任意开发 Agent） ──MCP──┐
                            ├── TCA Server：C + Webhook + SQLite
M（外部协调 Agent） ──MCP──┘
GitHub / GitLab ──Webhook───┘
```

TCA Docker 镜像不包含 M。`BasicRuleChecker` 只是 C 内部的确定性安全规则，用来检查显式依赖和路径重叠，不做语义协调。

## V1 能力

- 接收 GitHub Claim 分支 Webhook，并校验 HMAC 签名；
- 由 C 维护 Claim 状态机、权限和 SQLite 审计记录；
- 用基础规则检查显式依赖和路径重叠；
- 在 external 模式下生成协调任务，等待独立 M 处理；
- 通过 MCP 向 A 提供状态、执行、心跳、进度和完成工具；
- 通过 MCP 向 M 提供领取任务和提交协调建议工具；
- 将 Decision 写回 `.tca/decision.yaml`；
- 用一个 ASGI 进程提供 MCP、Webhook、Claim API 和健康检查；
- 支持 uv 本地环境和 Docker 部署。

## 仓库协议

项目管理员只需初始化并提交一次：

```text
AGENTS.md
.tca/
├── project.yaml
├── protocol.md
├── claim.example.yaml
└── agreements/
```

成员 clone 或 pull 后会得到相同协议，不需要各自修改 Markdown。每位成员只需在自己的 Agent 中配置 MCP 地址和个人 Token；凭证不能提交到 Git。

## 本地开发

```bash
uv sync
uv run pytest
cp .env.example .env
```

启动组合服务：

```bash
set -a
source .env
set +a
uv run tca-app
```

接口：

- `POST /mcp`：MCP Streamable HTTP；
- `POST /webhooks/github`：GitHub push Webhook；
- `GET /api/claims/{claim_id}`：权威 Claim 状态；
- `GET /health`：健康检查。

## 两种协调模式

`.env` 中使用：

```text
TCA_COORDINATION_MODE=external
```

- `external`：无硬冲突的 Claim 保持 `assessing`，等待外部 M 通过 MCP 给出建议；
- `rules`：仅用于开发和测试。基础规则未发现冲突时，C 自动批准。

## Docker

```bash
docker build -t team-code-agent .
docker run --rm -p 8000:8000 --env-file .env -v tca-data:/data team-code-agent
```

## Claim 分支

从 `.tca/claim.example.yaml` 复制 Claim，在 `tca/claim/<claim-id>` 分支中保存为 `.tca/claim.yaml` 并 push。Git 平台随后通过 Webhook 通知 C。

完整协议见 [`.tca/protocol.md`](.tca/protocol.md)，产品模型见 [`docs/ideas/project-idea.md`](docs/ideas/project-idea.md)。
