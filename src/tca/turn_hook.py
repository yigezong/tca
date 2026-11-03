"""Codex Stop hook: inspect the local worktree without involving the coding agent."""

from __future__ import annotations

import json
import subprocess
import sys
import urllib.parse
from pathlib import Path

import yaml

from .codex_setup import SetupError, _project_api, _tca_token, git_root, local_db
from .controller import Controller
from .domain import ClaimState
from .rules import BasicRuleChecker
from .store import ClaimStore
from .worktree_digest import build_worktree_digest


def capture_turn(payload: dict[str, object]) -> dict[str, object] | None:
    if payload.get("hook_event_name") != "Stop":
        return None
    cwd = payload.get("cwd")
    if not isinstance(cwd, str):
        return None
    try:
        repo = git_root(Path(cwd))
    except SetupError:
        return None
    project_file = repo / ".tca" / "project.yaml"
    identity_file = repo / ".tca" / "agent-id"
    if not project_file.is_file() or not identity_file.is_file():
        return None
    author = identity_file.read_text(encoding="utf-8").strip()
    if author == "agent-m" or not author.startswith("agent-"):
        return None
    config = yaml.safe_load(project_file.read_text(encoding="utf-8"))
    if not isinstance(config, dict) or not isinstance(config.get("mcp"), dict):
        raise ValueError("invalid TCA project configuration")
    project_id = config.get("project_id")
    if not isinstance(project_id, str) or not project_id:
        raise ValueError("invalid TCA project ID")
    branch_result = subprocess.run(
        ["git", "-C", str(repo), "symbolic-ref", "--quiet", "--short", "HEAD"],
        capture_output=True, text=True, check=False, timeout=10,
    )
    if branch_result.returncode:
        return None
    branch = branch_result.stdout.strip()
    if config.get("mode") == "remote":
        mcp_url = config["mcp"].get("url")
        if not isinstance(mcp_url, str):
            raise ValueError("remote TCA URL is missing")
        token = _tca_token(mcp_url, str(config["mcp"].get("token_env", "TCA_TOKEN")))
        path = "/api/projects/" + urllib.parse.quote(project_id.lower(), safe="/")
        found = _project_api(
            mcp_url, token, path + "/turn-capture?branch="
            + urllib.parse.quote(branch, safe=""),
        ).get("claim")
        if not isinstance(found, dict):
            return None
        claim = found.get("claim")
        if not isinstance(claim, dict) or found.get("state") != "executing":
            return None
        digest = build_worktree_digest(repo, claim)
        turn_id = payload.get("turn_id")
        digest["turn_id"] = turn_id if isinstance(turn_id, str) else ""
        return _project_api(
            mcp_url, token,
            path + "/claims/" + urllib.parse.quote(str(claim["claim_id"]), safe="")
            + "/turn-capture", {"snapshot": digest},
        )
    if config.get("mode") == "local":
        database = local_db()
        if not database.is_file():
            return None
        store = ClaimStore(database)
        try:
            matches = [record for record in store.list_active(project_id)
                       if record.state is ClaimState.EXECUTING
                       and record.claim.author == author
                       and record.claim.work_branch == branch]
            if not matches:
                return None
            if len(matches) != 1:
                raise ValueError("more than one executing Claim uses this branch")
            record = matches[0]
            digest = build_worktree_digest(repo, record.claim.to_mapping())
            turn_id = payload.get("turn_id")
            digest["turn_id"] = turn_id if isinstance(turn_id, str) else ""
            result = Controller(store, BasicRuleChecker()).report_worktree_snapshot(
                project_id, record.claim.claim_id, digest
            )
            return {"worktree_snapshot": result.worktree_snapshot}
        finally:
            store.close()
    raise ValueError("unknown TCA project mode")


def main() -> None:
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("invalid Codex Stop hook payload")
        capture_turn(payload)
    except (ValueError, OSError, SetupError, KeyError, subprocess.TimeoutExpired) as exc:
        print(f"TCA turn capture failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print("{}")


if __name__ == "__main__":
    main()
