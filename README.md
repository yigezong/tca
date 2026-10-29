# Team Code Agent (TCA)

TCA coordinates coding agents before they edit a shared Git repository. Developers submit Claims directly to the TCA MCP server, wait for an authoritative decision, work in their own Git branches, and report completion. Git holds code history; TCA holds coordination history.

- [中文说明](README_zh.md)
- [Codex setup](docs/codex-native.md)
- [Protocol guide](docs/tca-guide.md)
- [GitHub App setup](docs/github-app-setup.md)
- [Demo video](docs/assets/tca-process-demo.mp4)

## One protocol, two deployments

| Deployment | State | Authentication |
| --- | --- | --- |
| One computer | Local SQLite, shared by separate worktrees | Local OS user; no cross-user isolation |
| Multiple computers | Hosted TCA Server with Postgres | GitHub-listed Ed25519 SSH key at login, then a short-lived TCA-only session |

Both deployments use the same Claim sequence: submit_claim → wait_for_decision → start_execution → edit/commit/push → complete_claim. No Claim branch or Claim webhook is required. A completed Claim is ready for integration; it is not yet merged.

The repository creator starts a TCA project and can delegate the single M role to another repository member. M reads Claims and code and advises developers; M does not commit business code. Developers with repository write access can join as A. The server rechecks repository permissions on every project operation.

## Local Codex setup

From this source checkout:

~~~bash
sh scripts/install-codex.sh /absolute/path/to/your-git-repo
~~~

This registers a local MCP server and installs repository guidance. Commit the generated shared guidance, keep .tca/agent-id local, and open a new Codex task. Run one A per computer, optionally alongside one M in a separate worktree. Local mode does not authenticate different humans and is not a multi-computer service.

## Hosted setup

Create a GitHub App with repository Metadata: read access, install it only on repositories that use TCA, and generate an App private key. The App is used to check current collaborator permissions. It does not need Contents or write access. Set these server variables:

~~~bash
TCA_AUTH_MODE=github
DATABASE_URL=postgresql://...
TCA_ALLOWED_HOSTS=tca.example.com
TCA_GITHUB_APP_ID=your-app-id
TCA_GITHUB_APP_PRIVATE_KEY=your-app-private-key-pem
~~~

Deploy with uv run tca-app. Protect the App private key and database as server secrets. TCA does not accept a user's GitHub personal token as an MCP credential. In particular, never export the output of gh auth token as TCA_TOKEN.

Each person installs the CLI from this checkout and logs in with a GitHub-listed Ed25519 SSH key:

~~~bash
uv tool install --python 3.12 .
tca-codex login --server-url https://tca.example.com/mcp --login YOUR_GITHUB_LOGIN
~~~

On first login, the CLI creates a dedicated key in ~/.tca/identity. If GitHub does not yet list its public key, the CLI prints the key and asks you to add it at https://github.com/settings/keys; retry login afterward. The private key stays on your computer. Login signs a one-time challenge and stores an eight-hour TCA-only session in ~/.tca/sessions with owner-only file permissions. The server stores only its token hash.

Make this session available to Codex when it starts:

~~~bash
export TCA_TOKEN="$(tca-codex token --server-url https://tca.example.com/mcp)"
~~~

If Codex Desktop does not inherit terminal variables, set TCA_TOKEN in its launch environment before opening a new task. This token only authorizes TCA operations; it is not a GitHub or model API key.

The repository creator, who needs GitHub admin permission, starts any installed repository:

~~~bash
tca-codex start --repo /absolute/path/to/repo --server-url https://tca.example.com/mcp
~~~

The creator can delegate M to a member who has repository read access:

~~~bash
tca-codex delegate-m --project-id OWNER/REPO --login MANAGER_GITHUB_LOGIN --server-url https://tca.example.com/mcp
~~~

The new M logs in and joins from a separate read-only worktree. Other developers with write access log in and join from their own computers:

~~~bash
tca-codex projects --server-url https://tca.example.com/mcp
tca-codex join --repo /absolute/path/to/repo --server-url https://tca.example.com/mcp
tca-codex doctor --repo /absolute/path/to/repo
~~~

A project member can inspect Claim history through the MCP tools list_claim_revisions and list_claim_events. These are append-only application records in the TCA database; they are not an immutable external notarization. Claims are visible to authorized TCA project members, and the server operator can access the database. Do not put secrets in Claims.

**Migrating an existing remote project:** install the GitHub App and deploy the updated server first. Each member runs tca-codex login, exports TCA_TOKEN, then runs start or join with --replace-local-config --replace-mcp to replace the old token environment name in that worktree. The project database keeps existing Claims; older revisions that were overwritten before this version cannot be recovered. The migration records each existing Claim's current snapshot.

## Git integration

A writes code to its own Git work branch. A calls complete_claim after committing and pushing. The team's integrator merges normally. The project creator can verify the result and call integrate_claim. Optional signed GitHub push webhooks remain a compatibility integration signal; they are not the Claim submission path. Enabling the legacy webhook reader may require extra repository access and is not needed for the standard flow.

Older shared-token and Claim-webhook implementations remain in the code for existing trusted deployments; they are not the hosted onboarding path. Do not copy the old protocol into new repositories.

## Verification

Run uv sync and uv run pytest. The test suite covers the controller, SSH challenge replay, TCA session authentication, metadata-only GitHub App permission checks, M delegation, Claim history, and local/hosted API behavior. A live GitHub App installation and multi-computer deployment still require an integration check in the target environment.
