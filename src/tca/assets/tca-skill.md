---
name: tca-coordination
description: Coordinate parallel coding tasks in a Git repo initialized with TCA. Use when changing shared code, submitting a claim, coordinating two developers, or acting as the TCA manager.
---

# TCA in Codex

Read `.tca/project.yaml` and `.tca/agent-id` before working. The latter is local to this worktree. Use only the MCP server named by `mcp.server` in project.yaml (for example `tca_local`); another TCA connection may belong to another team. Keep using Codex's normal chat, code, terminal, model, and login. Never ask the user for a separate model API key.

## Developer (`agent-id` other than `agent-m`)

1. Read the request and relevant source. Identify the planned paths, owned symbols, behavior contracts, and dependencies yourself. Do not start shared edits yet.
2. Read `project_id` from `.tca/project.yaml`. Fetch the latest default branch when a remote exists, select the Git baseline for your own work branch, and record its full SHA as `base_commit`. Create a unique `claim_id` and work branch. Call TCA `submit_claim` with `claim_id`, `project_id`, `author` (the local agent ID), `base_commit`, `work_branch`, `goal`, and `planned_paths`; add `owned_symbols`, `behavior_contracts`, and `coordination_group` when relevant. The old `expected_group_size` field remains accepted for compatibility but does not gate a group. Both local and hosted deployments submit directly through MCP; neither needs a Claim branch or Claim webhook.
3. Call `wait_for_decision`. Read the returned constraints and `allowed_actions`. Call `start_execution` only when it is allowed. If coordination is pending, wait or ask the manager to process it. Do not infer permission from a previous chat.
4. Work only in your own branch/worktree. Respect the ownership and interface contract. In hosted mode, after `start_execution`, run `tca-client watch-worktree CLAIM_ID --interval 5` in a separate terminal until completion. It sends bounded names and API-shape changes from your uncommitted worktree, not source text. After meaningful behavior changes, run `tca-client snapshot CLAIM_ID --summary "brief behavior update"` so M has the semantic detail a Git digest cannot infer. Keep summaries free of secrets and code excerpts. Report progress during long work; use `publish_handoff` for a stable interface needed by another agent.
   When coordination or integration causes actual rework, report `rework_started` and `rework_finished` through `record_subgoal_event` with the same `subgoal_id`; do not label a pre-execution Claim revision as coding rework.
5. Run appropriate checks, commit and push the result when a remote exists. In hosted mode, run `tca-client complete CLAIM_ID` from your worktree; it checks the branch, base commit, clean tree, pushed head, and changed paths, then reports completion evidence. If using MCP directly, perform those same Git checks and pass `evidence` with `base_commit`, full `commit_sha`, and `changed_paths` to `complete_claim`. In local mode, call `complete_claim` after the checks. Tell the user the branch, commit, checks, and any remaining integration work. A completed claim is not yet merged; an expired claim is not completed.

If TCA MCP is unavailable, stop before shared edits and report the connection problem. If the task has no remote, use local Git branches; do not claim to have pushed or integrated anything.

## Manager (`agent-m`)

Use a separate, read-only checkout or worktree. Read the claims and needed shared code, then call `wait_for_coordination_task(project_id)`. Group membership remains open until execution starts; inspect every currently active Claim in the declared `coordination_group` before deciding. An executing peer's `worktree_snapshot` shows recent uncommitted path and Python API-shape changes plus an A-authored summary. Refresh that peer with `get_claim_state` just before deciding, check `observed_at`, and treat missing or old snapshots as unknown, never as proof that no edits occurred. Publish a separate, actionable `publish_coordination_advice` message for each owning A, then submit each Claim's decision. Do not let the first A start while another group Claim is still `assessing`.

If one group member is already `executing` when a new Claim arrives, do not interrupt, message, or change that executing Claim. Any other same-group Claim that has not started is reopened with the newcomer; review and advise all of them against the current group. Old advice remains in history but is no longer current. Copy `coordination_round` and `claim.revision` from each leased task into `publish_coordination_advice.coordination_round` and `submit_coordination_decision.expected_round` / `expected_revision`. A stale task must be fetched again. Keep developer tasks viable where possible. Do not modify business code. If there is no task, tell the user and wait for the next request; a Codex chat is not an always-on daemon.
