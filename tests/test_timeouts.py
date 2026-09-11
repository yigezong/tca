from datetime import UTC, datetime, timedelta

import pytest

from tca.controller import Controller
from tca.domain import Claim, ClaimState, Decision
from tca.rules import BasicRuleChecker
from tca.store import ClaimStore


class FakeClock:
    def __init__(self, start: datetime | None = None) -> None:
        self.now = start or datetime(2026, 1, 1, tzinfo=UTC)

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)

    def __call__(self) -> datetime:
        return self.now


def make_claim(
    claim_id: str,
    paths: tuple[str, ...],
    *,
    project_id: str = "team/project",
    dependencies: tuple[str, ...] = (),
) -> Claim:
    return Claim(
        claim_id=claim_id,
        project_id=project_id,
        author=f"agent-{claim_id}",
        base_commit="abc123",
        work_branch=f"feature/agent-{claim_id}/{claim_id}",
        goal=f"implement {claim_id}",
        planned_paths=paths,
        dependencies=dependencies,
    )


def make_controller(**kwargs) -> Controller:
    return Controller(ClaimStore(), BasicRuleChecker(), **kwargs)


def approve(controller: Controller, claim_id: str, project_id: str = "team/project") -> None:
    controller.submit_coordination_decision(
        project_id,
        claim_id,
        Decision(ClaimState.APPROVED, "semantic review found no conflict"),
    )


def test_execution_timeout_auto_completes_and_releases_waiter() -> None:
    clock = FakeClock()
    controller = make_controller(
        execution_timeout=60, heartbeat_timeout=30, m_decision_timeout=3600, clock=clock
    )

    first = controller.submit_claim(make_claim("claim-1", ("src/session/**",)))
    approve(controller, first.claim.claim_id)
    controller.start_execution("team/project", "claim-1")
    second = controller.submit_claim(make_claim("claim-2", ("src/session/api.py",)))
    assert second.state is ClaimState.WAITING

    # A1 finished working but never calls complete_claim; the deadline elapses.
    clock.advance(61)
    state = controller.get_claim_state("team/project", "claim-1")

    assert state["state"] == "completed"
    assert "deadline" in state["decision"]["reason"]

    # A2 is released: re-queued for M and can be approved right away.
    released = controller.store.get("team/project", "claim-2")
    assert released.state is ClaimState.ASSESSING
    approve(controller, "claim-2")
    assert controller.store.get("team/project", "claim-2").state is ClaimState.APPROVED


def test_heartbeat_extends_execution_deadline() -> None:
    clock = FakeClock()
    controller = make_controller(
        execution_timeout=60, heartbeat_timeout=30, m_decision_timeout=3600, clock=clock
    )
    first = controller.submit_claim(make_claim("claim-1", ("src/session/**",)))
    approve(controller, first.claim.claim_id)
    controller.start_execution("team/project", "claim-1")

    clock.advance(59)
    controller.report_progress("team/project", "claim-1", "halfway through")

    # Past the original 60s deadline, but the heartbeat keeps the claim alive.
    clock.advance(29)
    still_running = controller.get_claim_state("team/project", "claim-1")
    assert still_running["state"] == "executing"
    assert still_running["last_heartbeat"] != ""
    assert still_running["progress"] == "halfway through"

    # Heartbeat window elapses: now overdue.
    clock.advance(2)
    overdue = controller.get_claim_state("team/project", "claim-1")
    assert overdue["state"] == "completed"


def test_m_decision_timeout_falls_back_to_rule_checker() -> None:
    clock = FakeClock()
    controller = make_controller(
        m_decision_timeout=60, execution_timeout=3600, clock=clock
    )
    record = controller.submit_claim(make_claim("claim-1", ("src/api/**",)))
    assert record.state is ClaimState.ASSESSING

    # M never submits a decision; C resolves the claim deterministically.
    clock.advance(61)
    state = controller.get_claim_state("team/project", "claim-1")

    assert state["state"] == "approved"
    assert "M decision deadline" in state["decision"]["reason"]
    assert controller.get_coordination_task("team/project") is None


def test_m_decision_timeout_with_wait_cycle_escalates_to_human() -> None:
    clock = FakeClock()
    controller = make_controller(
        m_decision_timeout=60, execution_timeout=3600, clock=clock
    )
    controller.submit_claim(make_claim("claim-2", ("src/x/**",)))
    waiting = controller.submit_claim(make_claim("claim-1", ("src/x/**",)))
    assert waiting.state is ClaimState.WAITING

    clock.advance(61)
    state = controller.get_claim_state("team/project", "claim-2")

    assert state["state"] == "needs_human"
    assert "wait cycle" in state["decision"]["reason"]


def test_start_execution_and_complete_claim_are_idempotent() -> None:
    controller = make_controller()
    first = controller.submit_claim(make_claim("claim-1", ("src/a/**",)))
    approve(controller, first.claim.claim_id)

    started = controller.start_execution("team/project", "claim-1")
    started_again = controller.start_execution("team/project", "claim-1")
    assert started_again.state is ClaimState.EXECUTING
    assert started_again.revision == started.revision

    completed = controller.complete_claim("team/project", "claim-1")
    completed_again = controller.complete_claim("team/project", "claim-1")
    assert completed_again.state is ClaimState.COMPLETED
    assert completed_again.revision == completed.revision


def test_submit_claim_replay_is_idempotent() -> None:
    controller = make_controller()
    claim = make_claim("claim-1", ("src/a/**",))

    first = controller.submit_claim(claim)
    replayed = controller.submit_claim(claim)

    assert replayed.state == first.state
    assert replayed.revision == first.revision


@pytest.mark.anyio
async def test_mcp_exposes_report_progress_tool() -> None:
    from tca.mcp_server import create_mcp

    mcp = create_mcp(make_controller())
    tools = await mcp.list_tools()

    assert "report_progress" in {tool.name for tool in tools}


def test_needs_human_disabled_maps_m_submission_to_approved() -> None:
    controller = make_controller(allow_needs_human=False)
    record = controller.submit_claim(make_claim("claim-1", ("src/api/**",)))
    assert record.state is ClaimState.ASSESSING

    # M says "can't decide, needs human" — but the server has needs_human
    # disabled, so C resolves the claim deterministically.
    controller.submit_coordination_decision(
        "team/project",
        "claim-1",
        Decision(ClaimState.NEEDS_HUMAN, "M cannot decide reliably"),
    )

    state = controller.get_claim_state("team/project", "claim-1")
    assert state["state"] == "approved"
    assert "needs_human is disabled" in state["decision"]["reason"]


def test_needs_human_disabled_cycle_breaks_deterministically() -> None:
    clock = FakeClock()
    controller = make_controller(
        m_decision_timeout=60,
        execution_timeout=3600,
        allow_needs_human=False,
        clock=clock,
    )
    controller.submit_claim(make_claim("claim-2", ("src/x/**",)))
    waiting = controller.submit_claim(make_claim("claim-1", ("src/x/**",)))
    assert waiting.state is ClaimState.WAITING

    clock.advance(61)
    state = controller.get_claim_state("team/project", "claim-2")

    assert state["state"] == "approved_with_constraints"
    assert "needs_human is disabled" in state["decision"]["reason"]
    assert "feature/agent-claim-1/claim-1" in state["decision"]["constraints"]


def test_needs_human_enabled_keeps_human_escalation() -> None:
    controller = make_controller()
    record = controller.submit_claim(make_claim("claim-1", ("src/api/**",)))
    assert record.state is ClaimState.ASSESSING

    controller.submit_coordination_decision(
        "team/project",
        "claim-1",
        Decision(ClaimState.NEEDS_HUMAN, "M cannot decide reliably"),
    )

    state = controller.get_claim_state("team/project", "claim-1")
    assert state["state"] == "needs_human"
