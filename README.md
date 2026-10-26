# Team Code Agent (TCA)

TCA is a Git-first coordination service for coding agents. Before agents edit a shared repository, TCA records what each agent intends to change, coordinates interfaces and ownership, and tracks when work may start and finish. It keeps code changes in normal Git branches and worktrees.

- [中文说明](README_zh.md)
- [Codex native setup (Chinese)](docs/codex-native.md)
- [Operations guide (Chinese)](docs/tca-guide.md)
- [End-to-end demo video](docs/assets/tca-process-demo.mp4)

The 64-second demo shows an M agent reading A1/A2 claims, parallel development, Git integration, and joint acceptance. It also retains a real merge conflict and a follow-up repair, so it does not claim that TCA eliminates conflicts or always reduces total elapsed time.

## Roles

| Role | Responsibility |
| --- | --- |
| C / TCA Server | Authoritative claim state, coordination queue, and access control |
| A / Developer agent | Publishes a claim, waits for permission, edits its own branch, and reports completion |
| M / Manager agent | Reads claims and repository context, then publishes advice and a coordination decision; it does not edit business code |
| Integrator | Merges completed work through the team's normal Git process |

## Quick start: use TCA from Codex

TCA's local preview uses Codex's existing login and model settings. It needs no model API key, hosted TCA service, PostgreSQL, webhook, or demo web app.

From the TCA source checkout, initialize a Git repository:

```bash
sh scripts/install-codex.sh /absolute/path/to/your-git-repo
```

The installer registers a local `tca_local` MCP server and adds repository guidance. Then restart the Codex task so it loads the new MCP server and instructions. See [the setup guide](docs/codex-native.md) for details and troubleshooting.

### One computer: one A, optionally one M

Use at most one developer agent A on a computer because it uses that computer's configured GitHub identity. You can run one M on the same computer; M is read-only and does not push or commit business code. Run A and M in separate worktrees, sharing the local SQLite coordination database. In Codex, keep one project for the business repository and create separate A and M conversations under that repository.

For teams with multiple developers, each developer computer runs one A and connects to the same remote TCA Server. Do not model two GitHub identities as two local A agents on one computer.

## Coordination timing

For a declared group (`coordination_group` and `expected_group_size > 1`), TCA waits for the group claims if no member has started execution. M sees the full group and publishes advice and a decision for each A. A cannot start while another group claim is still awaiting M's decision.

If A1 is already executing when A2 submits, TCA leaves A1 untouched. M sees the active context, but publishes guidance and a decision only for the new A2 claim. This keeps the later submission asynchronous without interrupting work already in progress.

Use the same `coordination_group` for claims that belong together. When two claims are expected before work starts, set `expected_group_size: 2`; TCA holds the first claim until its peer arrives. If A2 appears after A1 is approved but before A1 starts, TCA reopens A1 for group review so M can advise both. If A1 is already executing, TCA leaves it alone and asks M to advise only A2. Independent work should use the default group size of one.

## Architecture

```text
Developer Codex (A) ── MCP ──┐
Manager Codex (M) ───── MCP ──┼── TCA Controller ── SQLite (local) / Postgres (server)
GitHub push/webhook ──────────┘
```

The local preview stores coordination state in `~/.tca/coordination.db`. A remote deployment uses PostgreSQL and can authenticate each member with GitHub. The older shared-token mode remains available for trusted teams. A `completed` claim means the branch is ready to integrate; it does not mean it has been merged.

## Run the server locally

```bash
uv sync
uv run pytest
uv run tca-app
```

Open `http://localhost:8000/` for the project page and claim status view.

## Deploy the shared server

For cross-computer teams, deploy this repository and configure:

```bash
TCA_AUTH_MODE=github
DATABASE_URL=replace-with-postgres-url
TCA_ALLOWED_HOSTS=tca.example.com
```

The server verifies the caller's GitHub identity and repository permissions on each operation. The person starting a project needs admin access to its GitHub repository and becomes that project's M. Other users with write access can join independently; each is bound to one `agent-<GitHub login>` A identity and at most one active Claim per project. Editing a local agent ID cannot impersonate another user or M. See the [GitHub repository API](https://docs.github.com/en/rest/repos/repos) for the permission fields used.
For automatic integration confirmation on default-branch pushes, also configure `GITHUB_WEBHOOK_SECRET` and `GITHUB_TOKEN` and install a signed webhook on the business repository. Otherwise M can verify the merge and call `integrate_claim`.

Until a package is published, install the CLI from this source checkout. Sign in with `gh auth login`, then make your GitHub token available to Codex when it starts:

```bash
git clone https://github.com/yigezong/tca.git
cd tca
uv tool install --python 3.12 .
gh auth login
export TCA_GITHUB_TOKEN="$(gh auth token)"
```

**Repository owner: start any repository you administer:**

```bash
tca-codex start --repo /absolute/path/to/repo \
  --server-url https://tca.example.com/mcp
tca-codex doctor --repo /absolute/path/to/repo
```

`start` uses the repository's GitHub `origin`, registers the project, and configures this worktree as M. M reads code and coordinates claims but does not commit business code. Start a separate project for each repository you administer.

To start a repository before cloning it, use `tca-codex start --project-id owner/repo --server-url https://tca.example.com/mcp`. Clone later and run `tca-codex join --repo /path/to/repo --server-url https://tca.example.com/mcp` to configure the owner's M worktree.

**Developer: find and join accessible projects:**

```bash
tca-codex projects --server-url https://tca.example.com/mcp
tca-codex join --repo /absolute/path/to/cloned-repo \
  --server-url https://tca.example.com/mcp
tca-codex doctor --repo /absolute/path/to/cloned-repo
```

`projects` lists started projects the signed-in GitHub user can access. `join` requires repository write access and configures the user's sole A identity. Each developer uses their own computer and GitHub account. Keep `TCA_GITHUB_TOKEN` out of Git; it is a repository access credential, not a model API key. Commit shared project guidance, while `.tca/agent-id` remains local to each worktree. GitHub mode accepts claims through authenticated MCP; signed default-branch webhooks can still confirm integration, but webhook Claim submission is disabled.

If the repository was initialized in local preview mode, add `--replace-local-config` to `start` or `join` to migrate this project's `.tca/project.yaml` and the current worktree's `.tca/agent-id`. Other differing files remain protected. Run the owner's migration in a separate M worktree.

### Legacy shared-token deployment

For a single trusted team, configure:

```bash
TCA_API_TOKEN=replace-with-random-token
DATABASE_URL=replace-with-postgres-url
TCA_ALLOWED_HOSTS=tca.example.com
```

For GitHub integration, also set `GITHUB_WEBHOOK_SECRET` and `GITHUB_TOKEN`. A remote client uses `TCA_API_TOKEN`; this is a TCA service credential, not a model API key.

The shared-token mode does not provide project-level authorization. Use GitHub mode above for independent repository permissions.

## Current limitations

- The native installer currently targets macOS and Linux and requires downloading the TCA source checkout.
- M is a normal Codex task and must be started by a person; it is not a background daemon.
- Local SQLite coordinates agents on one computer only. Cross-computer teams need a shared TCA Server.
- The end-to-end video is a recorded demonstration, not a performance guarantee.
- The GitHub authorization flow has automated tests, but still needs a live two-computer deployment check.
