# TCA: coordinate developers before their code is merged

[中文说明](README_zh.md) · [Demo video](docs/assets/tca-process-demo.mp4)

## The problem

One developer is changing an inventory module and may change its `reserve()` API. The work is still on that person's computer or branch; it has not been merged into the default branch. Another developer must build checkout from an old snapshot. They can guess an incompatible API, or wait for the entire upstream feature to merge.

TCA lets developers **disclose intent before editing shared code**. The upstream developer submits a Claim describing planned files, behavior, interfaces, and uncertainty. A coordinator uses those disclosures to assign responsibility and advise the downstream developer. When exact code is needed, the upstream developer can commit and push a small, stable **interface checkpoint** on their own branch without finishing or merging the whole feature.

**Limit:** TCA cannot read uncommitted files on someone else's computer. A Claim tells the team what its author plans to do; a Git checkpoint makes an actual interface available. If the interface is still unstable, the downstream developer must wait for a checkpoint instead of assuming the latest code is visible.

## Roles and the harness

The **harness** is the set of rules around a normal coding agent such as Codex: when to submit a Claim, which state permits editing, what context the coordinator sees, and how to record handoffs and completion. Without it, agents often work from isolated Git snapshots and independently invent conflicting interfaces.

| Role | Responsibility |
| --- | --- |
| **A, developer** | Submit a Claim, then develop and push code on their own Git branch after authorization. One computer runs at most one code-committing A. |
| **M, coordinator** | Read Claims and necessary code, clarify ownership, interface, dependencies, and acceptance conditions. M does not commit product code or see uncommitted files. |
| **C, controller** | Maintain the authoritative state, check rules and permissions, apply M's decisions, and reject stale coordination results. C does not replace code review or Git integration. |
| **Project creator** | Start a project with repository admin permission, optionally delegate M, and verify final integration. |

A creator with write permission may run **one read-only M and one A in separate worktrees**. This permits a two-person, two-computer experiment: one person runs M and A1; the other runs A2. Each person commits only with their own GitHub identity.

## Protocol and key states

1. A sends a **Claim** directly to the TCA MCP server: goal, planned paths and symbols, behavior contract, dependencies, Git base commit, and work branch. A Claim branch or webhook is unnecessary.
2. C checks declared boundaries. When coordination is needed, M leases a task, sends advice to the relevant A, and submits a decision. A calls `wait_for_decision` or `get_claim_state`. Shared edits require `allowed_actions` to contain `start_execution`, followed by an actual `start_execution` call.
3. A develops on their own branch and may report progress. If another A needs a stable interface early, the owner commits and pushes an interface checkpoint, then calls `publish_handoff` with its SHA, exported symbols, and behavior contract. The owner can continue developing afterward.
4. A commits and pushes completed work. In hosted mode, `tca-client complete CLAIM_ID` checks the branch, base, clean worktree, remote head, changed paths, and declared top-level Python symbols. The Metadata-only GitHub App cannot independently read private diffs, so code and the reported evidence still need review. The team merges normally; the creator verifies integration.

| State | Meaning |
| --- | --- |
| `assessing` | Under review or renewed coordination; editing is not allowed. |
| `approved` / `approved_with_constraints` | Approved, optionally with explicit constraints; A must still call `start_execution`. |
| `waiting` | Waiting for a declared dependency or interface handoff. |
| `executing` | A is working and can report progress or publish a handoff. |
| `needs_human` / `blocked` | Human requirements clarification is needed / the current plan cannot proceed. |
| `expired` | Execution timed out without verified delivery; late Git evidence may be supplied. |
| `completed` / `integrated` | A reported delivery / the creator confirmed integration. **Completion does not mean merge.** |

### How conflicts are handled

- **Before coding:** M assigns ownership of shared files, functions, and data formats. One A can own the interface while another implements against it. A dependent task can wait for a small interface checkpoint instead of a full feature merge.
- **Staggered arrivals:** If A1 is executing, A2 has advice but has not started, and A3 joins the same group, C leaves A1 alone but reopens A2 and A3 for joint coordination. Earlier advice remains in history but is no longer current; decisions with old rounds or revisions are rejected. If A2 is already executing, M coordinates only the newcomer.
- **After delivery:** Developers still perform Git integration and run meaningful tests. TCA reduces blind conflicts; it does not promise automatic merges or hide failing tests. C does not automatically release work with a declared shared boundary when M times out.

## Setup

### Hosted C: multiple computers

The shared service needs HTTPS, a shared database (Postgres is recommended), and a GitHub App installed on each participating repository with **Metadata: read** only. The App checks current repository permissions; it needs neither Contents nor write access. Each person installs Codex and the TCA CLI:

```bash
git clone https://github.com/yigezong/tca.git
cd tca
uv tool install --python 3.12 --force .
tca-codex login --server-url https://YOUR_TCA_HOST/mcp --login YOUR_GITHUB_LOGIN
```

On first login, add the **public** Ed25519 key printed by the CLI to your GitHub SSH keys if asked, then retry. The private key remains local. A short-lived TCA-only session is stored locally; the server stores its hash. Make it available to the environment that launches Codex:

```bash
export TCA_TOKEN="$(tca-codex token --server-url https://YOUR_TCA_HOST/mcp)"
```

Never put the session token, GitHub token, or private key in chat, repository files, or screenshots. Codex Desktop may need `TCA_TOKEN` set in its own launch environment and a new chat. For the two-person experiment, deploy the version of C that permits a writing creator to act as M and A, and update the CLI on both computers.

The creator runs `tca-codex start --repo PATH --server-url https://YOUR_TCA_HOST/mcp` in an M checkout, then commits the generated `AGENTS.md`, `.agents/skills/`, `.tca/project.yaml`, and `.gitignore`. `.tca/agent-id` remains local. In a separate checkout, the same person runs `tca-codex join --role a --repo PATH --server-url ...`; the other developer runs `tca-codex join --repo PATH --server-url ...`. Use `tca-codex doctor --repo PATH` in every checkout. The creator may instead delegate M to another repository reader.

### C without cloud hosting

For one computer, run `sh scripts/install-codex.sh /absolute/path/to/repo` from this repository. The `tca_local` server and SQLite coordinate worktrees on that computer; they do not authenticate separate humans or share state across computers. To put C on one participant's laptop **and still use two computers**, run it as a service reachable by both, with a shared database, HTTPS endpoint, and the same identity checks. A local stdio server on only one computer is insufficient.

## Two-computer live exercise: an unmerged upstream API

The starter project is [`examples/stockroom-starter`](examples/stockroom-starter). Catalog and stock queries work; reservation still uses an old result without an idempotency key, and `Checkout.place_order()` is unfinished. A1 changes the reservation API while A2 builds checkout. A2 initially sees only the old default branch.

**Prepare before the presentation.** From the TCA repository root, create a separate private business repository (replace the GitHub login and choose an unused repository name):

The `gh` commands use GitHub CLI; run `gh auth login` first if needed. This GitHub credential is separate from your TCA session.
If a demo repository already contains this starter, skip the copy, `git init`, and `gh repo create` steps and configure the M checkout directly.

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

Grant the second developer repository write access and install the TCA GitHub App on **this new repository**. Use the current checkout as M and run `tca-codex start --repo . --server-url https://YOUR_TCA_HOST/mcp`; commit and push its generated shared guidance. Then clone the updated repository separately:

```bash
# First person's computer, from the parent directory of the M checkout
gh repo clone YOUR_GITHUB_LOGIN/tca-stockroom-live stockroom-a1
tca-codex join --role a --repo ./stockroom-a1 --server-url https://YOUR_TCA_HOST/mcp
tca-codex doctor --repo ./stockroom-a1

# Second person's computer
gh repo clone YOUR_GITHUB_LOGIN/tca-stockroom-live stockroom-a2
tca-codex join --repo ./stockroom-a2 --server-url https://YOUR_TCA_HOST/mcp
tca-codex doctor --repo ./stockroom-a2
```

Both people first log in to TCA, expose their own `TCA_TOKEN` to Codex, and open **new** Codex chats. Open M, A1, and A2 chats in their respective checkouts.

| Order | Operator | Checkpoint |
| --- | --- | --- |
| 1 | First person, **A1** chat | Submit upstream Claim; stop before editing. |
| 2 | First person, **M** chat | Review A1, decide; then resume A1. |
| 3 | After A1 reports local uncommitted changes, second person, **A2** chat | Submit downstream Claim from old `main`; do not edit yet. |
| 4 | First person, **M** chat | Advise A2 using both Claims; do not pretend to see A1's uncommitted files. |
| 5 | Second person, **A2** chat | Start after approval and implement independent parts. |
| 6 | First person, **A1** chat | Push an interface checkpoint and publish a handoff; do not merge `main`. |
| 7 | Both A finish; creator integrates temporarily | Merge both branches in a temporary integration branch, run tests, inspect behavior. |

**Prompt 1 — A1:**

> Own inventory reservation. Repeating the same `order_id` must never deduct stock twice; insufficient stock or invalid quantity must not deduct it. You expect to change the call and result contract of `Inventory.reserve` while keeping `available()` semantics. Submit a TCA Claim first, covering `src/stockroom/inventory.py`, `src/stockroom/contracts.py`, and inventory tests, in coordination group `stockroom-checkout`. Disclose your proposed interface and uncertainty. Wait for authorization before editing. Do not commit, push, or merge until I ask.

**Prompt 2 — M:**

> As read-only M, lease the `stockroom-checkout` coordination task. Read the Claim and necessary baseline code, clarify A1's interface ownership and constraints, and submit advice and a decision with the current round and revision. Do not edit product code or claim to see uncommitted work.

Then tell A1: “Query my Claim again. If allowed, call `start_execution`, implement inventory and tests under the agreed contract, and stop with local uncommitted changes. Report changed files; do not commit, push, or merge.” Wait for that report before Prompt 3.

**Prompt 3 — A2:**

> Implement `Checkout.place_order(sku, quantity, order_id)`. Success needs a verifiable order result; failure must not create a paid order or double-deduct stock. Upstream is changing reservation, but its work is not merged. Submit a TCA Claim in group `stockroom-checkout`; truthfully include `src/stockroom/contracts.py` if you might need to change it, and declare your dependency on the upstream reservation interface. Wait for C/M. Do not start by coding against the old `reserve()` API in `main`.

**Prompt 4 — M:**

> A2 has submitted. Read A1 and A2 Claims and the available Git code. A1 is executing and its local implementation has not been merged. Give A2 actionable advice: who owns the inventory contract, what A2 may edit, how checkout handles success/failure and retries, and what must wait for an interface checkpoint. Use the leased task's `coordination_round` and `claim.revision`. Do not interrupt A1 or claim to see its uncommitted files.

**Prompt 5 — A2:**

> Query my current Claim; if allowed, call `start_execution`. Implement checkout and tests within the boundary M assigned. Do not edit A1's inventory or contract files. If the exact upstream API is not in a fetchable commit, complete independent work and report the dependency instead of guessing uncommitted code.

**Prompt 6 — A1:**

> Commit and push the smallest stable reservation interface checkpoint to your feature branch, without merging `main`. Call `publish_handoff` with the commit SHA, exported symbols, and behavior contract. Keep unfinished implementation on your branch and provide A2 the branch and SHA; do not ask A2 to rely on files only on your computer.

**Prompt 7 — each A:** “Read the published interface checkpoint, finish your own code and tests, commit and push your branch, and report completion through TCA. Do not edit modules owned by the other A.” The creator then merges both branches in a **temporary** integration branch, runs all tests and checks the behavior. Merge into `main` and confirm `integrated` only after review; record whether any failure came from the interface, implementation, tests, or Git merge.

Observe three results: **whether A2 receives useful guidance without seeing uncommitted code; whether an unmerged checkpoint unblocks A2; and whether the combined code works.** Record Claim-to-start, checkpoint-to-downstream-validation, and integration times. This is a live protocol exercise, not proof of time savings by itself.
