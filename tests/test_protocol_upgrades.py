from __future__ import annotations

import subprocess
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tca.completion import python_symbols, validate_completion, verify_local_git
from tca.controller import Controller
from tca.domain import Claim, ClaimState, Decision, SubgoalEvent, SubgoalEventType
from tca.metrics import claim_metrics, project_metrics
from tca.rules import BasicRuleChecker
from tca.store import ClaimStore


def claim(name: str, path: str, group: str = "") -> Claim:
    return Claim(
        claim_id=name, project_id="team/project", author=f"agent-{name}",
        base_commit="abc123", work_branch=f"feature/agent-{name}/{name}",
        goal=f"Build {name}", planned_paths=(path,),
        coordination_group=group, expected_group_size=9 if group else 1,
    )


def test_open_group_reassesses_only_before_execution() -> None:
    c = Controller(ClaimStore(), BasicRuleChecker())
    first = c.submit_claim(claim("a1", "src/api.py", "feature"))
    assert first.state is ClaimState.ASSESSING  # no nine-member deadlock
    c.submit_coordination_decision("team/project", "a1", Decision(ClaimState.APPROVED, "API owner"))
    c.submit_claim(claim("a2", "src/adapter.py", "feature"))
    assert c.store.get("team/project", "a1").state is ClaimState.ASSESSING
    c.submit_coordination_decision("team/project", "a1", Decision(ClaimState.APPROVED, "reviewed with A2"))
    c.submit_coordination_decision("team/project", "a2", Decision(ClaimState.APPROVED, "adapter owner"))
    c.start_execution("team/project", "a1")
    c.start_execution("team/project", "a2")
    before = c.store.get("team/project", "a1").revision
    late = c.submit_claim(claim("a3", "tests/test_api.py", "feature"))
    assert late.state is ClaimState.APPROVED
    assert c.store.get("team/project", "a1").revision == before
    task_ids = []
    while task := c.get_coordination_task("team/project"):
        task_ids.append(task.claim.claim.claim_id)
        c.submit_coordination_decision(
            "team/project", task.claim.claim.claim_id,
            Decision(ClaimState.APPROVED, "advice observed"),
        )
    assert "a3" in task_ids


def test_group_timeout_does_not_auto_approve_shared_boundary() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    clock = lambda: now
    c = Controller(ClaimStore(), BasicRuleChecker(), clock=clock, m_decision_timeout=30)
    c.submit_claim(claim("a1", "src/api.py", "feature"))
    c.submit_claim(claim("a2", "src/api.py", "feature"))
    now += timedelta(seconds=31)
    state = c.get_claim_state("team/project", "a1")
    assert state["state"] == "needs_human"
    assert "start_execution" not in state["allowed_actions"]


def test_group_timeout_without_human_review_blocks() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    c = Controller(
        ClaimStore(), BasicRuleChecker(), clock=lambda: now,
        m_decision_timeout=30, allow_needs_human=False,
    )
    c.submit_claim(claim("a1", "src/api.py", "feature"))
    c.submit_claim(claim("a2", "src/api.py", "feature"))
    now += timedelta(seconds=31)
    assert c.get_claim_state("team/project", "a1")["state"] == "blocked"


def test_group_timeout_releases_independent_work() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    c = Controller(ClaimStore(), BasicRuleChecker(), clock=lambda: now, m_decision_timeout=30)
    c.submit_claim(claim("a1", "src/api.py", "feature"))
    c.submit_claim(claim("a2", "src/report.py", "feature"))
    now += timedelta(seconds=31)
    assert c.get_claim_state("team/project", "a1")["state"] == "approved"
    assert c.get_claim_state("team/project", "a2")["state"] == "approved"


def test_late_shared_boundary_waits_for_m_without_interrupting_running_a() -> None:
    c = Controller(ClaimStore(), BasicRuleChecker())
    c.submit_claim(claim("a1", "src/api.py", "feature"))
    c.submit_coordination_decision("team/project", "a1", Decision(ClaimState.APPROVED, "API owner"))
    c.start_execution("team/project", "a1")
    prior = c.store.get("team/project", "a1").revision
    late = c.submit_claim(claim("a2", "src/api.py", "feature"))
    assert late.state is ClaimState.ASSESSING
    assert c.store.get("team/project", "a1").revision == prior
    with pytest.raises(ValueError, match="illegal transition"):
        c.start_execution("team/project", "a2")


def test_completion_evidence_rejects_out_of_scope_paths() -> None:
    c = Controller(ClaimStore(), BasicRuleChecker())
    c.submit_claim(claim("a1", "src/api.py"))
    c.start_execution("team/project", "a1")
    with pytest.raises(ValueError, match="outside Claim scope"):
        c.complete_claim("team/project", "a1", {
            "base_commit": "abc123", "commit_sha": "a" * 40,
            "changed_paths": ["src/api.py", "src/secret.py"],
        })
    assert c.store.get("team/project", "a1").state is ClaimState.EXECUTING
    c.complete_claim("team/project", "a1", {
        "base_commit": "abc123", "commit_sha": "b" * 40,
        "changed_paths": ["src/api.py"],
    })
    events = c.store.list_claim_events("team/project", "a1")
    assert events[-1]["payload"]["metadata"]["completion_evidence"]["commit_sha"] == "b" * 40


def test_python_symbol_evidence_rejects_unowned_function() -> None:
    scoped = replace(claim("a1", "src/api.py"), owned_symbols=("normalize",))
    evidence = {"base_commit": "abc123", "commit_sha": "a" * 40,
                "changed_paths": ["src/api.py"],
                "changed_symbols": ["src/api.py::normalize", "src/api.py::delete_all"]}
    with pytest.raises(ValueError, match="delete_all"):
        validate_completion(scoped, evidence)
    assert "Worker.run" in python_symbols(
        "class Worker:\n    def run(self):\n        return 1\n", "src/api.py"
    )


def test_local_git_verifier_requires_branch_scope_and_push(tmp_path: Path) -> None:
    def git(repo: Path, *args: str) -> str:
        return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()

    remote = tmp_path / "remote.git"
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    git(repo, "config", "user.email", "tca@example.test")
    git(repo, "config", "user.name", "TCA Test")
    git(repo, "remote", "add", "origin", str(remote))
    (repo / "src").mkdir()
    (repo / "src/api.py").write_text("BASE = True\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "base")
    base = git(repo, "rev-parse", "HEAD")
    branch = "feature/agent-a1/a1"
    git(repo, "checkout", "-b", branch)
    (repo / "src/api.py").write_text("BASE = True\nFEATURE = True\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "feature")
    spec = {"base_commit": base, "work_branch": branch, "planned_paths": ["src/api.py"]}
    with pytest.raises(ValueError, match="push"):
        verify_local_git(repo, spec)
    git(repo, "push", "origin", branch)
    evidence = verify_local_git(repo, spec)
    assert evidence["changed_paths"] == ["src/api.py"]
    (repo / "src/secret.py").write_text("SECRET = True\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "out of scope")
    git(repo, "push", "origin", branch)
    with pytest.raises(ValueError, match="outside Claim scope"):
        verify_local_git(repo, spec)


def test_project_metrics_count_completed_and_phase_times() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    def event(seconds: int, kind: str, payload: dict) -> dict:
        return {"created_at": (start + timedelta(seconds=seconds)).isoformat(),
                "event_type": kind, "payload": payload}
    row = claim_metrics([
        event(0, "claim_submitted", {}),
        event(10, "state_transition", {"from": "assessing", "to": "approved"}),
        event(12, "state_transition", {"from": "approved", "to": "executing"}),
        event(40, "state_transition", {"from": "executing", "to": "completed"}),
        event(50, "state_transition", {"from": "completed", "to": "integrated"}),
    ], [
        SubgoalEvent("team/project", "a1", "fix-1", "agent-a1",
                     SubgoalEventType.REWORK_STARTED, "repair", created_at=(start + timedelta(seconds=20)).isoformat()),
        SubgoalEvent("team/project", "a1", "fix-1", "agent-a1",
                     SubgoalEventType.REWORK_FINISHED, "repair", created_at=(start + timedelta(seconds=30)).isoformat()),
    ])
    assert row["coordination_seconds"] == 10
    assert row["execution_seconds"] == 28
    assert row["reported_rework_count"] == 1
    assert row["reported_rework_seconds"] == 10
    assert project_metrics([row])["median_end_to_end_seconds"] == 50


@pytest.mark.anyio
async def test_project_metrics_is_available_to_mcp_members() -> None:
    from tca.mcp_server import create_mcp

    c = Controller(ClaimStore(), BasicRuleChecker())
    c.submit_claim(claim("a1", "src/api.py"))
    _, data = await create_mcp(c).call_tool("get_project_metrics", {"project_id": "team/project"})
    assert data["claims"] == 1
    assert data["completed"] == 0
