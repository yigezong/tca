from tca.controller import Controller
from tca.domain import Claim, ClaimState, Decision
from tca.rules import BasicRuleChecker
from tca.store import ClaimStore

import pytest


def make_claim(
    claim_id: str,
    paths: tuple[str, ...],
    *,
    project_id: str = "team/project",
    version: int = 1,
    dependencies: tuple[str, ...] = (),
) -> Claim:
    return Claim(
        claim_id=claim_id,
        project_id=project_id,
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


def approve_claim(
    controller: Controller, claim_id: str, project_id: str = "team/project"
) -> None:
    controller.submit_coordination_decision(
        project_id,
        claim_id,
        Decision(ClaimState.APPROVED, "semantic review found no conflict"),
    )


def test_claim_without_hard_conflicts_is_queued_for_m() -> None:
    controller = make_controller()

    record = controller.submit_claim(make_claim("claim-1", ("src/login/**",)))

    assert record.state is ClaimState.ASSESSING
    state = controller.get_claim_state("team/project", "claim-1")
    assert state["allowed_actions"] == ["get_claim_state"]
    task = controller.get_coordination_task("team/project")
    assert task is not None
    assert task.claim.claim.claim_id == "claim-1"


def test_overlapping_claim_waits_and_reassesses_after_completion() -> None:
    controller = make_controller()
    first = controller.submit_claim(make_claim("claim-1", ("src/session/**",)))
    approve_claim(controller, first.claim.claim_id)
    controller.start_execution(first.claim.project_id, first.claim.claim_id)

    second = controller.submit_claim(make_claim("claim-2", ("src/session/api.py",)))

    assert second.state is ClaimState.WAITING
    assert second.decision is not None
    assert second.decision.wait_for == ("claim-1",)

    controller.complete_claim("team/project", "claim-1")

    reassessed = controller.store.get("team/project", "claim-2")
    assert reassessed.state is ClaimState.ASSESSING
    task = controller.get_coordination_task("team/project")
    assert task is not None
    assert task.claim.claim.claim_id == "claim-2"


def test_resubmitted_claim_requires_new_version() -> None:
    controller = make_controller()
    controller.submit_claim(make_claim("claim-1", ("src/login/**",)))
    approve_claim(controller, "claim-1")

    try:
        controller.submit_claim(make_claim("claim-1", ("src/login/**",), version=1))
    except ValueError as error:
        assert "version must be greater" in str(error)
    else:
        raise AssertionError("same claim version should be rejected")

    amended = controller.submit_claim(
        make_claim("claim-1", ("src/login/**", "src/router/**"), version=2)
    )
    assert amended.claim.version == 2
    assert amended.state is ClaimState.ASSESSING


def test_same_claim_id_can_exist_in_different_projects() -> None:
    controller = make_controller()

    first = controller.submit_claim(
        make_claim("claim-1", ("src/login/**",), project_id="team/project-a")
    )
    second = controller.submit_claim(
        make_claim("claim-1", ("src/login/**",), project_id="team/project-b")
    )

    assert first.claim.project_id == "team/project-a"
    assert second.claim.project_id == "team/project-b"
    assert controller.get_claim_state("team/project-a", "claim-1")["claim"][
        "project_id"
    ] == "team/project-a"
    assert controller.get_claim_state("team/project-b", "claim-1")["claim"][
        "project_id"
    ] == "team/project-b"


def test_m_gets_task_and_c_applies_its_decision() -> None:
    controller = make_controller()
    submitted = controller.submit_claim(make_claim("claim-1", ("src/login/**",)))

    assert submitted.state is ClaimState.ASSESSING
    task = controller.get_coordination_task("team/project")
    assert task is not None
    assert task.claim.claim.claim_id == "claim-1"

    decided = controller.submit_coordination_decision(
        "team/project",
        "claim-1",
        Decision(ClaimState.APPROVED, "semantic review found no conflict"),
    )

    assert decided.state is ClaimState.APPROVED
    assert controller.get_coordination_task("team/project") is None


def test_m_cannot_force_execution() -> None:
    controller = make_controller()
    controller.submit_claim(make_claim("claim-1", ("src/login/**",)))

    with pytest.raises(ValueError, match="cannot move"):
        controller.submit_coordination_decision(
            "team/project",
            "claim-1",
            Decision(ClaimState.EXECUTING, "skip approval"),
        )
