# Team Code Agent

Team Code Agent（TCA）是一个 Git-first 的多 Agent 协作控制服务。它让多个 coding agent 在同一个项目里工作时，先发布意图、协调边界，再执行代码修改。

当前阶段先不使用 TCA 开发 TCA 本体；TCA 本体按普通 Git 流程开发。TCA 协议优先用于另一个“被测 repo”，等系统稳定后再考虑自举。

因此本仓库根目录不保留 `.tca/` 或 `.codex/` MCP 活配置；这些文件只应出现在被测 repo 中。被测 repo 接入样例见 [templates/test-repo](templates/test-repo)。

## 当前核心

```text
A / M Agent ── MCP ──┐
                     ▼
Git provider ─ webhook ─► TCA Server / C ─► 状态数据库
                     ▲
                     └── 读取远端 Claim 分支
```

- Git provider 保存代码、Claim 分支和团队约定；当前已实现 GitHub adapter。
- TCA Server 运行 C（Controller）、MCP 接口和 webhook。
- 状态数据库保存 Claim 状态机和审计记录；当前本地用 SQLite，部署用 Postgres。
- A 是开发 Agent，M 是外部协调 Agent；二者都通过 MCP 和 C 交互。

## 本地开发

```bash
uv sync
uv run python -m pytest
uv run tca-app
```

未设置 `DATABASE_URL` 时使用本地 SQLite；设置后使用 Postgres。

## 部署

当前已验证的托管组合：Vercel + Neon/Postgres。GitHub webhook 只给独立被测 repo 使用，不给 TCA 本体仓库自举使用。后续可以替换成自托管 ASGI 服务、自己的 Postgres 和其他 Git provider adapter。

TCA Server 本体的部署配置见 [docs/server-deployment.md](docs/server-deployment.md)。

## 主要文档

- [docs/tca-guide.md](docs/tca-guide.md)：实现原理、文件地图、部署配置、被测 repo 接入方式。
- [docs/server-deployment.md](docs/server-deployment.md)：只讲 `fringsoo/tca` 作为 TCA Server 的 Vercel/环境变量配置。
- [templates/test-repo](templates/test-repo)：被测 repo 的最小接入模板，包含 `AGENTS.md` 和 `.tca/` 协议配置。
