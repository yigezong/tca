# TCA Server 部署配置

这份文档只讲 TCA Server 本体，也就是 `fringsoo/tca` 这个仓库怎么部署成一个 MCP/API 服务。被测 repo 接入先不在这里展开。

## 先分清两种 GitHub 关系

`fringsoo/tca` 现在是 **TCA Server 源码仓库**，不是被 TCA 管理的被测 repo。

所以它在 GitHub/Vercel 上应该只有这类关系：

```text
fringsoo/tca push main
  -> Vercel Git integration 自动部署 TCA Server
```

它不应该有这类关系：

```text
fringsoo/tca push tca/claim/*
  -> https://<tca-server>/webhooks/github
```

也就是说：你已经删掉 `fringsoo/tca` 自己指向 `/webhooks/github` 的 webhook，这是对的。以后 `/webhooks/github` 是给独立被测 repo 用的，不是给 TCA 本体用的。

## 当前先配什么

如果目标只是先把 TCA Server 跑起来，Vercel 里最少只需要三类环境变量：

```bash
TCA_API_TOKEN=...
DATABASE_URL=...
TCA_ALLOWED_HOSTS=...
```

含义：

| 变量 | 为什么需要 |
|---|---|
| `TCA_API_TOKEN` | A/M 调 `/mcp` 和 `/api` 的 Bearer token。当前 V1 是单服务共享 token |
| `DATABASE_URL` | 线上状态库。Vercel 上不要依赖本地 SQLite |
| `TCA_ALLOWED_HOSTS` | MCP transport security 的允许 Host。填用户实际访问的域名，例如 `tca-ten.vercel.app`，不要带 `https://` |

如果用了自定义域名，可以写多个：

```bash
TCA_ALLOWED_HOSTS=tca-ten.vercel.app,tca.example.com
```

## 当前先不要配什么

先不接被测 repo 时，可以先不配：

```bash
GITHUB_WEBHOOK_SECRET=
GITHUB_TOKEN=
```

不设置 `GITHUB_WEBHOOK_SECRET` 时，Server 会让 `/webhooks/github` 保持关闭状态。`/health` 和 `/mcp` 仍然可以用于验证服务本身。

## 什么时候再配 GitHub adapter

等我们真的创建被测 repo，例如 `tca-playground`，再回到 Vercel 加：

```bash
GITHUB_WEBHOOK_SECRET=...
GITHUB_TOKEN=...
```

然后只在被测 repo 的 GitHub Settings -> Webhooks 里配置：

```text
Payload URL: https://<tca-server-domain>/webhooks/github
Content type: application/json
Secret: 等于 GITHUB_WEBHOOK_SECRET
Events: Push events
```

`GITHUB_TOKEN` 不是 Vercel 部署源码用的 token；它是 TCA Server 去 GitHub 读取被测 repo 里 `.tca/claim.yaml` 的 token。私有被测 repo 需要它，公开 repo 可以暂时不设但会受 GitHub 匿名访问限制。

## 验证方式

部署后先看：

```bash
curl https://<tca-server-domain>/health
```

应该返回类似：

```json
{"status":"ok","store":"postgres"}
```

如果返回 `store: sqlite`，说明 Vercel 没有拿到 `DATABASE_URL`，线上状态不会可靠持久化。

`/mcp` 需要 Bearer token，普通浏览器直接打开失败是正常的。

## 现在的产品边界

当前实现更像一个小团队共享的 TCA Server，不是完整多租户 SaaS：

- 一个 `TCA_API_TOKEN` 保护所有 A/M MCP 调用；
- 一个 Server 数据库保存所有 Claim 状态；
- GitHub adapter 也是一组 server-wide 配置。

所以“给所有用户提供一个 TCA MCP 服务”在当前阶段应理解为：给我们控制的一组用户/被测 repo 提供同一个服务端点。真正公开服务还需要后续做项目级权限、多 token、用户隔离和审计后台。
