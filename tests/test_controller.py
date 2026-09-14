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
    coordination_group: str = "",
    expected_group_size: int = 1,
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
        coordination_group=coordination_group,
        expected_group_size=expected_group_size,
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


def test_leased_coordination_task_only_accepts_its_worker_decision() -> None:
    controller = make_controller()
    controller.submit_claim(make_claim("claim-1", ("src/login/**",)))
    task = controller.lease_coordination_task("team/project", "worker-1", 300)
    assert task is not None
    assert controller.lease_coordination_task("team/project", "worker-2", 300) is None

    with pytest.raises(ValueError, match="not leased"):
        controller.submit_coordination_decision(
            "team/project",
            "claim-1",
            Decision(ClaimState.APPROVED, "wrong worker"),
            worker_id="worker-2",
        )

    decided = controller.submit_coordination_decision(
        "team/project",
        "claim-1",
        Decision(ClaimState.APPROVED, "right worker"),
        worker_id="worker-1",
    )
    assert decided.state is ClaimState.APPROVED


def test_grouped_claims_require_one_shared_boundary_contract() -> None:
    controller = make_controller()
    first = controller.submit_claim(
        make_claim(
            "a1", ("src/a.py",), coordination_group="pair-1", expected_group_size=2
        )
    )
    controller.submit_coordination_decision(
        "team/project",
        "a1",
        Decision(ClaimState.APPROVED, "first", boundary_contract="pair-1"),
    )
    with pytest.raises(ValueError, match="waiting for all claims"):
        controller.start_execution("team/project", "a1")

    second = controller.submit_claim(
        make_claim(
            "a2", ("src/b.py",), coordination_group="pair-1", expected_group_size=2
        )
    )
    controller.submit_coordination_decision(
        "team/project",
        "a2",
        Decision(ClaimState.APPROVED, "second", boundary_contract="pair-1"),
    )
    controller.start_execution("team/project", first.claim.claim_id)
    assert controller.store.get("team/project", "a1").state is ClaimState.EXECUTING


def test_grouped_claims_reject_mismatched_boundary_contracts() -> None:
    controller = make_controller()
    for claim_id, path in (("a1", "src/a.py"), ("a2", "src/b.py")):
        controller.submit_claim(
            make_claim(
                claim_id, (path,), coordination_group="pair-1", expected_group_size=2
            )
        )
    controller.submit_coordination_decision(
        "team/project", "a1", Decision(ClaimState.APPROVED, "first", boundary_contract="one")
    )
    controller.submit_coordination_decision(
        "team/project", "a2", Decision(ClaimState.APPROVED, "second", boundary_contract="two")
    )
    with pytest.raises(ValueError, match="boundary_contracts do not match"):
        controller.start_execution("team/project", "a1")


def test_privileged_review_resolves_needs_human_with_calibrated_uncertainty() -> None:
    controller = make_controller()
    claim = controller.submit_claim(make_claim("claim-1", ("src/login/**",)))
    controller.request_requirements_alignment(
        "team/project",
        claim.claim.claim_id,
        claim.claim.author,
        "the public default is ambiguous",
    )

    resolved = controller.resolve_privileged_review(
        "team/project",
        "claim-1",
        Decision(
            ClaimState.APPROVED_WITH_CONSTRAINTS,
            "P found no breaking interaction",
            constraints=("Preserve Image.save default behavior.",),
            uncertainty=0.8,
        ),
    )

    assert resolved.state is ClaimState.APPROVED_WITH_CONSTRAINTS
    assert resolved.decision is not None
    assert "no strong preference" in resolved.decision.reason
    assert resolved.decision.uncertainty == 0.8


def test_m_cannot_escalate_to_needs_human_but_owner_can_request_alignment() -> None:
    controller = make_controller()
    claim = controller.submit_claim(make_claim("claim-1", ("src/login/**",)))

    with pytest.raises(ValueError, match="M cannot move"):
        controller.submit_coordination_decision(
            claim.claim.project_id,
            claim.claim.claim_id,
            Decision(ClaimState.NEEDS_HUMAN, "M cannot own requirement alignment"),
        )

    with pytest.raises(ValueError, match="only the Claim author"):
        controller.request_requirements_alignment(
            claim.claim.project_id,
            claim.claim.claim_id,
            "agent-other",
            "consumer contract is underspecified",
        )

    aligned = controller.request_requirements_alignment(
        claim.claim.project_id,
        claim.claim.claim_id,
        claim.claim.author,
        "consumer contract is underspecified",
    )
    assert aligned.state is ClaimState.NEEDS_HUMAN
    assert aligned.decision is not None
    assert aligned.decision.reason.startswith("A requested requirements alignment:")


def test_owner_can_pause_execution_for_requirements_alignment_and_resume() -> None:
    controller = make_controller()
    claim = controller.submit_claim(make_claim("claim-1", ("src/login/**",)))
    approve_claim(controller, claim.claim.claim_id)
    controller.start_execution(claim.claim.project_id, claim.claim.claim_id)

    aligned = controller.request_requirements_alignment(
        claim.claim.project_id,
        claim.claim.claim_id,
        claim.claim.author,
        "the positional argument semantics are ambiguous",
    )
    assert aligned.state is ClaimState.NEEDS_HUMAN

    resolved = controller.resolve_privileged_review(
        claim.claim.project_id,
        claim.claim.claim_id,
        Decision(ClaimState.APPROVED_WITH_CONSTRAINTS, "P clarified the interface"),
    )
    assert resolved.state is ClaimState.APPROVED_WITH_CONSTRAINTS

    resumed = controller.start_execution(claim.claim.project_id, claim.claim.claim_id)
    assert resumed.state is ClaimState.EXECUTING


def test_privileged_review_only_resolves_needs_human() -> None:
    controller = make_controller()
    controller.submit_claim(make_claim("claim-1", ("src/login/**",)))

    with pytest.raises(ValueError):
        controller.resolve_privileged_review(
            "team/project",
            "claim-1",
            Decision(ClaimState.APPROVED, "P cannot skip the escalation"),
        )


def test_overlapping_claim_is_released_directly_after_completion() -> None:
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
    assert reassessed.state is ClaimState.APPROVED_WITH_CONSTRAINTS
    assert reassessed.decision is not None
    assert reassessed.decision.constraints == (
        "Inspect and incorporate the completed overlapping work before editing: "
        "feature/agent-claim-1/claim-1",
    )
    assert controller.get_coordination_task("team/project") is None


def test_new_overlap_sees_completed_unintegrated_claim() -> None:
    controller = make_controller()
    first = controller.submit_claim(make_claim("claim-1", ("src/session/**",)))
    approve_claim(controller, first.claim.claim_id)
    controller.start_execution(first.claim.project_id, first.claim.claim_id)
    controller.complete_claim(first.claim.project_id, first.claim.claim_id)

    second = controller.submit_claim(make_claim("claim-2", ("src/session/api.py",)))

    assert second.state is ClaimState.ASSESSING
    task = controller.get_coordination_task("team/project")
    assert task is not None
    assert task.active_claims == ()
    assert [(record.claim.claim_id, record.state) for record in task.completed_claims] == [
        ("claim-1", ClaimState.COMPLETED)
    ]


def test_integrated_claim_is_removed_from_coordination_context() -> None:
    controller = make_controller()
    first = controller.submit_claim(make_claim("claim-1", ("src/session/**",)))
    approve_claim(controller, first.claim.claim_id)
    controller.start_execution(first.claim.project_id, first.claim.claim_id)
    completed = controller.complete_claim(first.claim.project_id, first.claim.claim_id)
    assert completed.state is ClaimState.COMPLETED
    assert controller.get_claim_state("team/project", "claim-1")[
        "allowed_actions"
    ] == []

    integrated = controller.integrate_claim(
        first.claim.project_id, first.claim.claim_id
    )
    second = controller.submit_claim(make_claim("claim-2", ("src/session/api.py",)))

    assert integrated.state is ClaimState.INTEGRATED
    assert controller.get_claim_state("team/project", "claim-1")[
        "allowed_actions"
    ] == []
    task = controller.get_coordination_task("team/project")
    assert task is not None
    assert task.active_claims == ()
    assert task.completed_claims == ()


def test_claim_cannot_be_integrated_before_completion() -> None:
    controller = make_controller()
    claim = controller.submit_claim(make_claim("claim-1", ("src/session/**",)))

    with pytest.raises(ValueError, match="assessing -> integrated"):
        controller.integrate_claim(claim.claim.project_id, claim.claim.claim_id)


def test_integration_reassesses_claim_waiting_for_completed_work() -> None:
    controller = make_controller()
    first = controller.submit_claim(make_claim("claim-1", ("src/session/**",)))
    approve_claim(controller, first.claim.claim_id)
    controller.start_execution(first.claim.project_id, first.claim.claim_id)
    controller.complete_claim(first.claim.project_id, first.claim.claim_id)
    second = controller.submit_claim(make_claim("claim-2", ("src/session/api.py",)))
    controller.submit_coordination_decision(
        second.claim.project_id,
        second.claim.claim_id,
        Decision(
            ClaimState.WAITING,
            "wait for the completed branch to reach main",
            wait_for=(first.claim.claim_id,),
            conflicts_with=(first.claim.claim_id,),
        ),
    )

    controller.integrate_claim(first.claim.project_id, first.claim.claim_id)

    reassessed = controller.store.get(second.claim.project_id, second.claim.claim_id)
    assert reassessed.state is ClaimState.ASSESSING
    task = controller.get_coordination_task("team/project")
    assert task is not None
    assert task.claim.claim.claim_id == second.claim.claim_id
    assert task.active_claims == ()
    assert task.completed_claims == ()


def test_resubmitted_claim_replay_is_idempotent_and_amendment_needs_version() -> None:
    controller = make_controller()
    first = controller.submit_claim(make_claim("claim-1", ("src/login/**",)))
    approve_claim(controller, "claim-1")

    # Identical replay is idempotent: returns the current authoritative record.
    replayed = controller.submit_claim(
        make_claim("claim-1", ("src/login/**",), version=1)
    )
    assert replayed.state is ClaimState.APPROVED
    assert replayed.revision == controller.store.get(
        "team/project", "claim-1"
    ).revision

    # Changed content without a version bump is still rejected.
    try:
        controller.submit_claim(
            make_claim("claim-1", ("src/login/**", "src/router/**"), version=1)
        )
    except ValueError as error:
        assert "version must be greater" in str(error)
    else:
        raise AssertionError("changed claim without version bump should be rejected")

    amended = controller.submit_claim(
        make_claim("claim-1", ("src/login/**", "src/router/**"), version=2)
    )
    assert amended.claim.version == 2
    assert amended.state is ClaimState.ASSESSING


def test_claim_rejects_non_scoped_work_branch() -> None:
    controller = make_controller()
    value = make_claim("claim-1", ("src/login/**",)).to_mapping()
    value["work_branch"] = "feature/claim-1"

    with pytest.raises(ValueError, match="work_branch must be"):
        controller.submit_claim(Claim.from_mapping(value))


def test_claim_revision_cannot_change_its_baseline() -> None:
    controller = make_controller()
    original = make_claim("claim-1", ("src/login/**",))
    controller.submit_claim(original)
    value = original.to_mapping()
    value.update(base_commit="new-base", version=2)

    with pytest.raises(ValueError, match="cannot change: base_commit"):
        controller.submit_claim(Claim.from_mapping(value))


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
