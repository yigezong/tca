---
name: tca-coordination
description: Coordinate parallel coding tasks in a Git repo initialized with TCA. Use when changing shared code, submitting a claim, coordinating two developers, or acting as the TCA manager.
---

# TCA in Codex

Read `.tca/project.yaml` and `.tca/agent-id` before working. The latter is local to this worktree. Use only the MCP server named by `mcp.server` in project.yaml (for example `tca_local`); another TCA connection may belong to another team. Keep using Codex's normal chat, code, terminal, model, and login. Never ask the user for a separate model API key.

## Developer (`agent-id` other than `agent-m`)

1. Read the request and relevant source. Identify the planned paths, owned symbols, behavior contracts, and dependencies yourself. Do not start shared edits yet.
2. Read `project_id` from `.tca/project.yaml` and obtain the full current `git rev-parse HEAD` as `base_commit`. Create a unique `claim_id` and a Git work branch for this agent. Call TCA `submit_claim` with `claim_id`, `project_id`, `author` (the local agent ID), `base_commit`, `work_branch`, `goal`, and `planned_paths`; add `owned_symbols`, `behavior_contracts`, `coordination_group`, and `expected_group_size` when relevant. Local mode submits directly through MCP and needs no Claim branch or webhook.
3. Call `wait_for_decision`. Read the returned constraints and `allowed_actions`. Call `start_execution` only when it is allowed. If coordination is pending, wait or ask the manager to process it. Do not infer permission from a previous chat.
4. Work only in your own branch/worktree. Respect the ownership and interface contract. Report progress during long work; use `publish_handoff` for a stable interface needed by another agent.
5. Run appropriate checks, commit the result, then call `complete_claim`. Tell the user the branch, commit, checks, and any remaining integration work. A completed claim is not yet merged.

If TCA MCP is unavailable, stop before shared edits and report the connection problem. If the task has no remote, use local Git branches; do not claim to have pushed or integrated anything.

## Manager (`agent-m`)

Use a separate, read-only checkout or worktree. Read the claims and needed shared code, then call `wait_for_coordination_task(project_id)`. For a not-yet-executing group, inspect every active Claim in the declared `coordination_group` before deciding. Publish a separate, actionable `publish_coordination_advice` message for each owning A, then submit each Claim's decision. Do not let the first A start while another group Claim is still `assessing`.

If one group member is already `executing` when a new Claim arrives, do not interrupt, message, or change the existing Claim. Review the active context, publish advice only for the new Claim, and decide only that Claim. Keep both developer tasks viable where possible. Do not modify business code. If there is no task, tell the user and wait for the next request; a Codex chat is not an always-on daemon.
