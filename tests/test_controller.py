from tca.controller import Controller
from tca.domain import Claim, ClaimState, Decision
from tca.rules import BasicRuleChecker
from tca.store import ClaimStore

import pytest


def make_claim(
    claim_id: str,
    paths: tuple[str, ...],
    *,
    version: int = 1,
    dependencies: tuple[str, ...] = (),
) -> Claim:
    return Claim(
        claim_id=claim_id,
        project_id="team/project",
        author=f"agent-{claim_id}",
        base_commit="abc123",
        work_branch=f"feature/{claim_id}",
        goal=f"implement {claim_id}",
        planned_paths=paths,
        dependencies=dependencies,
        version=version,
    )


def make_controller() -> Controller:
    return Controller(ClaimStore(), BasicRuleChecker())


def approve_claim(controller: Controller, claim_id: str) -> None:
    controller.submit_coordination_decision(
        claim_id,
        Decision(ClaimState.APPROVED, "semantic review found no conflict"),
        "team-m",
    )


def test_claim_without_hard_conflicts_is_queued_for_m() -> None:
    controller = make_controller()

    record = controller.submit_claim(make_claim("claim-1", ("src/login/**",)))

    assert record.state is ClaimState.ASSESSING
    state = controller.get_claim_state("claim-1")
    assert state["allowed_actions"] == ["get_claim_state"]
    task = controller.get_coordination_task("team/project")
    assert task is not None
    assert task.claim.claim.claim_id == "claim-1"


def test_overlapping_claim_waits_and_reassesses_after_completion() -> None:
    controller = make_controller()
    first = controller.submit_claim(make_claim("claim-1", ("src/session/**",)))
    approve_claim(controller, first.claim.claim_id)
    controller.start_execution(first.claim.claim_id)

    second = controller.submit_claim(make_claim("claim-2", ("src/session/api.py",)))

    assert second.state is ClaimState.WAITING
    assert second.decision is not None
    assert second.decision.wait_for == ("claim-1",)

    controller.complete_claim("claim-1")

    reassessed = controller.store.get("claim-2")
    assert reassessed.state is ClaimState.ASSESSING
    task = controller.get_coordination_task("team/project")
    assert task is not None
    assert task.claim.claim.claim_id == "claim-2"
    events = controller.store.audit_events("claim-2")
    assert any(
        event["detail"].get("triggered_by") == "claim-1" for event in events
    )


def test_amend_requires_new_claim_version() -> None:
    controller = make_controller()
    controller.submit_claim(make_claim("claim-1", ("src/login/**",)))
    approve_claim(controller, "claim-1")

    try:
        controller.amend_claim(make_claim("claim-1", ("src/login/**",), version=1))
    except ValueError as error:
        assert "version must be greater" in str(error)
    else:
        raise AssertionError("same claim version should be rejected")

    amended = controller.amend_claim(
        make_claim("claim-1", ("src/login/**", "src/router/**"), version=2)
    )
    assert amended.claim.version == 2
    assert amended.state is ClaimState.ASSESSING


def test_m_gets_task_and_c_applies_its_decision() -> None:
    controller = make_controller()
    submitted = controller.submit_claim(make_claim("claim-1", ("src/login/**",)))

    assert submitted.state is ClaimState.ASSESSING
    task = controller.get_coordination_task("team/project")
    assert task is not None
    assert task.claim.claim.claim_id == "claim-1"

    decided = controller.submit_coordination_decision(
        "claim-1",
        Decision(ClaimState.APPROVED, "semantic review found no conflict"),
        "team-m",
    )

    assert decided.state is ClaimState.APPROVED
    assert controller.get_coordination_task("team/project") is None


def test_m_cannot_force_execution() -> None:
    controller = make_controller()
    controller.submit_claim(make_claim("claim-1", ("src/login/**",)))

    with pytest.raises(ValueError, match="cannot move"):
        controller.submit_coordination_decision(
            "claim-1",
            Decision(ClaimState.EXECUTING, "skip approval"),
            "bad-m",
        )
