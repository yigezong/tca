from tca.controller import Controller
from tca.coordinator import RuleBasedCoordinator
from tca.domain import Claim, ClaimState
from tca.store import ClaimStore


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
    return Controller(ClaimStore(), RuleBasedCoordinator())


def test_claim_without_conflicts_is_approved() -> None:
    controller = make_controller()

    record = controller.submit_claim(make_claim("claim-1", ("src/login/**",)))

    assert record.state is ClaimState.APPROVED
    state = controller.get_claim_state("claim-1")
    assert state["allowed_actions"] == [
        "start_execution",
        "amend_claim",
        "abandon_claim",
    ]


def test_overlapping_claim_waits_and_reassesses_after_completion() -> None:
    controller = make_controller()
    first = controller.submit_claim(make_claim("claim-1", ("src/session/**",)))
    controller.start_execution(first.claim.claim_id)

    second = controller.submit_claim(make_claim("claim-2", ("src/session/api.py",)))

    assert second.state is ClaimState.WAITING
    assert second.decision is not None
    assert second.decision.wait_for == ("claim-1",)

    controller.complete_claim("claim-1")

    reassessed = controller.store.get("claim-2")
    assert reassessed.state is ClaimState.APPROVED
    events = controller.store.audit_events("claim-2")
    assert any(
        event["detail"].get("triggered_by") == "claim-1" for event in events
    )


def test_amend_requires_new_claim_version() -> None:
    controller = make_controller()
    controller.submit_claim(make_claim("claim-1", ("src/login/**",)))

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
    assert amended.state is ClaimState.APPROVED
