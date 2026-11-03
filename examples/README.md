# TCA live example

P1 runs read-only M and A1 in separate checkouts; P2 runs A2 on a second computer.

## Two-computer live exercise: an unmerged upstream API

The starter project is this `examples/` directory. Catalog and stock queries work; reservation still uses an old result without an idempotency key, and `Checkout.place_order()` is unfinished. A1 changes the reservation API while A2 builds checkout. A2 initially sees only the old default branch.

**Prepare before the presentation.** From the TCA repository root, create a separate private business repository (replace the GitHub login and choose an unused repository name):

The `gh` commands use GitHub CLI; run `gh auth login` first if needed. This GitHub credential is separate from your TCA session.
If a demo repository already contains this starter, skip the copy, `git init`, and `gh repo create` steps and configure the M checkout directly.

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

Grant the second developer repository write access and install the TCA GitHub App on **this new repository**. Use the current checkout as M and run `tca-codex start --repo . --server-url https://YOUR_TCA_HOST/mcp`; commit and push its generated shared guidance. Then clone the updated repository separately:

```bash
# P1's computer, from the parent directory of the M checkout
gh repo clone YOUR_GITHUB_LOGIN/tca-stockroom-live stockroom-a1
tca-codex join --role a --repo ./stockroom-a1 --server-url https://YOUR_TCA_HOST/mcp
tca-codex doctor --repo ./stockroom-a1

# P2's computer
gh repo clone YOUR_GITHUB_LOGIN/tca-stockroom-live stockroom-a2
tca-codex join --repo ./stockroom-a2 --server-url https://YOUR_TCA_HOST/mcp
tca-codex doctor --repo ./stockroom-a2
```

P1 and P2 first log in to TCA, expose their own `TCA_TOKEN` to Codex, and open **new** Codex chats. Open M, A1, and A2 chats in their respective checkouts.

| Order | Operator | Checkpoint |
| --- | --- | --- |
| 1 | P1, **A1** chat | Submit upstream Claim; stop before editing. |
| 2 | P1, **M** chat | Review A1, decide; then resume A1. |
| 3 | After A1 publishes an uncommitted worktree digest, P2, **A2** chat | Submit downstream Claim from old `main`; do not edit yet. |
| 4 | P1, **M** chat | Check A1's latest digest and both Claims before advising A2; the digest is not source code. |
| 5 | P2, **A2** chat | Start after approval and implement independent parts. |
| 6 | P1, **A1** chat | Push an interface checkpoint and publish a handoff; do not merge `main`. |
| 7 | Both A finish; creator integrates temporarily | Merge both branches in a temporary integration branch, run tests, inspect behavior. |

**Prompt 1 — A1:**

> Own inventory reservation. Repeating the same `order_id` must never deduct stock twice; insufficient stock or invalid quantity must not deduct it. You expect to change the call and result contract of `Inventory.reserve` while keeping `available()` semantics. Submit a TCA Claim first, covering `src/stockroom/inventory.py`, `src/stockroom/contracts.py`, and inventory tests, in coordination group `stockroom-checkout`. Disclose your proposed interface and uncertainty. Wait for authorization before editing. Do not commit, push, or merge until I ask.

**Prompt 2 — M:**

> As read-only M, lease the `stockroom-checkout` coordination task. Read the Claim and necessary baseline code, clarify A1's interface ownership and constraints, and submit advice and a decision with the current round and revision. Do not edit product code or claim to see uncommitted work.

Then tell A1: “Query my Claim again. If allowed, call `start_execution`, implement inventory and tests under the agreed contract, and stop with local uncommitted changes. Do not commit, push, or merge.” Once A1 starts, run `tca-client watch-worktree CLAIM_ID --interval 5` in a **separate terminal in A1's worktree** (replace the Claim ID). When the behavior is clear, run `tca-client snapshot CLAIM_ID --summary "reserve is idempotent by order_id; failures do not deduct stock"`. Confirm that the server Claim has a `worktree_snapshot` before Prompt 3. Keep the watcher running until the Claim ends.

**Prompt 3 — A2:**

> Implement `Checkout.place_order(sku, quantity, order_id)`. Success needs a verifiable order result; failure must not create a paid order or double-deduct stock. Upstream is changing reservation, but its work is not merged. Submit a TCA Claim in group `stockroom-checkout`; truthfully include `src/stockroom/contracts.py` if you might need to change it, and declare your dependency on the upstream reservation interface. Wait for C/M. Do not start by coding against the old `reserve()` API in `main`.

**Prompt 4 — M:**

> A2 has submitted. Read A1 and A2 Claims and the available Git code. Refresh A1's Claim immediately before advising A2; inspect `worktree_snapshot` for API changes, the behavior summary, and `observed_at`. Give A2 actionable advice: who owns the inventory contract, what A2 may edit, how checkout handles success/failure and retries, and what must wait for an interface checkpoint. Treat a missing or stale digest as unknown, and do not treat it as runnable source code. Use the leased task's `coordination_round` and `claim.revision`. Do not interrupt A1.

**Prompt 5 — A2:**

> Query my current Claim; if allowed, call `start_execution`. Implement checkout and tests within the boundary M assigned. Do not edit A1's inventory or contract files. If the exact upstream API is not in a fetchable commit, complete independent work and report the dependency instead of guessing uncommitted code.

**Prompt 6 — A1:**

> Commit and push the smallest stable reservation interface checkpoint to your feature branch, without merging `main`. Call `publish_handoff` with the commit SHA, exported symbols, and behavior contract. Keep unfinished implementation on your branch and provide A2 the branch and SHA; do not ask A2 to rely on files only on your computer.

**Prompt 7 — each A:** “Read the published interface checkpoint, finish your own code and tests, commit and push your branch, and report completion through TCA. Do not edit modules owned by the other A.” The creator then merges both branches in a **temporary** integration branch, runs all tests and checks the behavior. Merge into `main` and confirm `integrated` only after review; record whether any failure came from the interface, implementation, tests, or Git merge.

Observe three results: **whether A2 receives useful guidance without seeing uncommitted code; whether an unmerged checkpoint unblocks A2; and whether the combined code works.** Record Claim-to-start, checkpoint-to-downstream-validation, and integration times. This is a live protocol exercise, not proof of time savings by itself.
