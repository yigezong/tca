# Team Code Agent

Team Code Agent（TCA）是一个 Git-first 的多 Agent 协作控制服务。Git 用来发布 Claim，MCP 用来让 A、M 与 C 交互，C 是唯一的状态权威。

## 当前本地结构

```text
A1 / A2 ──git push──> 本地 bare Git remote
                           │ post-receive hook
                           ▼
M（外部 Agent）──MCP──> TCA Server：C + SQLite <──MCP── A1 / A2
```

TCA Docker 镜像不包含 M。`BasicRuleChecker` 只是 C 内部的确定性安全规则，用来检查显式依赖和路径重叠，不做语义协调。

## V1 能力

- 接收本地 Git `post-receive` hook 发来的签名 Claim；
- 由 C 维护 Claim 状态机、权限和 SQLite 审计记录；
- 用基础规则检查显式依赖和路径重叠；
- 在 external 模式下生成协调任务，等待独立 M 处理；
- 通过 MCP 向 A 提供状态、执行、心跳、进度和完成工具；
- 通过 MCP 向 M 提供领取任务和提交协调建议工具；
- 保留可选的 GitHub Webhook 适配器；
- 用一个 ASGI 进程提供 MCP、Webhook、Claim API 和健康检查；
- 支持 uv 本地环境和 Docker 部署。

## 仓库协议

项目管理员初始化并提交一次：

```text
AGENTS.md
.codex/config.toml
.tca/
├── project.yaml
├── protocol.md
├── claim.example.yaml
├── hooks/post-receive
└── agreements/
```

成员 clone 或 pull 后会得到相同协议，不需要各自修改 Markdown。每位成员只需在自己的 Agent 中配置个人 `TCA_API_TOKEN`；凭证不能提交到 Git。

## 本地开发

```bash
uv sync
uv run pytest
cp .env.example .env
```

启动服务：

```bash
docker build -t team-code-agent:v1 .
docker run --rm -p 127.0.0.1:18080:8000 --env-file .env \
  -v tca-data:/data team-code-agent:v1
```

接口：

- `POST /mcp`：A、M 使用的 MCP Streamable HTTP；
- `POST /webhooks/git`：本地 Git hook 提交 Claim；
- `POST /webhooks/github`：可选的 GitHub push Webhook；
- `GET /api/claims/{claim_id}`：权威 Claim 状态；
- `GET /health`：健康检查。

## 本地 bare Git remote

创建 bare remote 后，将 `.tca/hooks/post-receive` 复制到它的 `hooks/` 目录，并在相邻的 `hooks/tca.env` 中配置：

```text
TCA_WEBHOOK_URL=http://127.0.0.1:18080/webhooks/git
TCA_WEBHOOK_SECRET=<与 TCA .env 相同的密钥>
TCA_PROJECT_ID=fringsoo/tca
```

A 在 `tca/claim/<claim-id>` 分支中提交 `.tca/claim.yaml` 并 push。hook 会读取该文件、计算 HMAC 签名并通知 C。

## 两种协调模式

- `external`：无硬冲突的 Claim 保持 `assessing`，等待外部 M 通过 MCP 给出建议；
- `rules`：仅用于开发和测试。基础规则未发现冲突时，C 自动批准。

完整协议见 [`.tca/protocol.md`](.tca/protocol.md)，产品模型见 [`docs/ideas/project-idea.md`](docs/ideas/project-idea.md)。
