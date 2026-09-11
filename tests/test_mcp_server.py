import asyncio

import pytest

from tca.controller import Controller
from tca.domain import Claim, ClaimState, Decision
from tca.mcp_server import create_mcp
from tca.rules import BasicRuleChecker
from tca.store import ClaimStore


def make_mcp():
    controller = Controller(ClaimStore(), BasicRuleChecker())
    return create_mcp(controller), controller


def make_claim(claim_id: str = "claim-1") -> Claim:
    return Claim(
        claim_id=claim_id,
        project_id="team/project",
        author="agent-a",
        base_commit="abc123",
        work_branch=f"feature/agent-a/{claim_id}",
        goal="make a coordinated change",
        planned_paths=("src/example/**",),
    )


@pytest.mark.anyio
async def test_mcp_exposes_wait_for_coordination_task() -> None:
    mcp, _ = make_mcp()

    tools = await mcp.list_tools()

    assert "wait_for_coordination_task" in {tool.name for tool in tools}
    assert "get_coordination_task" not in {tool.name for tool in tools}
    assert "submit_claim" in {tool.name for tool in tools}
    assert "integrate_claim" in {tool.name for tool in tools}


@pytest.mark.anyio
async def test_submit_claim_accepts_direct_claim() -> None:
    mcp, _ = make_mcp()
    claim = make_claim("claim-1").to_mapping()

    _, result = await mcp.call_tool("submit_claim", {"claim": claim})

    assert result["claim"]["project_id"] == "team/project"
    assert result["claim"]["claim_id"] == "claim-1"
    assert result["state"] in {"approved", "assessing"}


@pytest.mark.anyio
async def test_wait_for_coordination_task_returns_none_on_timeout() -> None:
    mcp, _ = make_mcp()

    result = await mcp.call_tool(
        "wait_for_coordination_task",
        {"project_id": "team/project", "timeout_seconds": 0},
    )

    assert result == ([], {"result": None})


@pytest.mark.anyio
async def test_wait_for_coordination_task_returns_pending_task() -> None:
    mcp, controller = make_mcp()
    controller.submit_claim(make_claim("claim-1"))

    _, result = await mcp.call_tool(
        "wait_for_coordination_task",
        {"project_id": "team/project", "timeout_seconds": 0},
    )

    task = result["result"]
    assert task["claim"]["claim"]["claim_id"] == "claim-1"
    assert task["active_claims"] == []
    assert task["completed_claims"] == []


@pytest.mark.anyio
async def test_mcp_leases_coordination_task_to_the_requesting_worker() -> None:
    mcp, controller = make_mcp()
    controller.submit_claim(make_claim("claim-1"))

    _, first = await mcp.call_tool(
        "wait_for_coordination_task",
        {"project_id": "team/project", "timeout_seconds": 0, "worker_id": "m-1"},
    )
    _, second = await mcp.call_tool(
        "wait_for_coordination_task",
        {"project_id": "team/project", "timeout_seconds": 0, "worker_id": "m-2"},
    )

    assert first["result"]["claim"]["claim"]["claim_id"] == "claim-1"
    assert second == {"result": None}


@pytest.mark.anyio
async def test_p_can_resolve_needs_human_with_uncertainty() -> None:
    mcp, controller = make_mcp()
    claim = controller.submit_claim(make_claim("claim-1"))
    _, requested = await mcp.call_tool(
        "request_requirements_alignment",
        {
            "project_id": claim.claim.project_id,
            "claim_id": claim.claim.claim_id,
            "requester": claim.claim.author,
            "reason": "the compatibility requirement is ambiguous",
        },
    )
    assert requested["state"] == "needs_human"

    _, result = await mcp.call_tool(
        "resolve_privileged_review",
        {
            "project_id": claim.claim.project_id,
            "claim_id": claim.claim.claim_id,
            "state": "approved_with_constraints",
            "reason": "P reviewed the private combined patch",
            "constraints": ["Preserve the public default behavior."],
            "uncertainty": 0.8,
            "alignment_owner": "agent-claim-1 owns the public parser interface",
            "alignment_compatibility": ["Keep the existing call form supported."],
            "alignment_acceptance": ["Invalid input raises ConfigError."],
            "alignment_split": ["Consumer adapts its call site after integration."],
        },
    )

    assert result["state"] == "approved_with_constraints"
    assert result["decision"]["uncertainty"] == 0.8
    assert result["decision"]["alignment_owner"] == "agent-claim-1 owns the public parser interface"


@pytest.mark.anyio
async def test_mcp_claim_tools_are_project_scoped() -> None:
    mcp, controller = make_mcp()
    controller.submit_claim(make_claim("claim-1"))

    _, result = await mcp.call_tool(
        "get_claim_state",
        {"project_id": "team/project", "claim_id": "claim-1"},
    )

    claim_state = result
    assert claim_state["claim"]["project_id"] == "team/project"
    assert claim_state["claim"]["claim_id"] == "claim-1"


@pytest.mark.anyio
async def test_wait_for_decision_waits_through_waiting_claims() -> None:
    mcp, controller = make_mcp()
    first = controller.submit_claim(make_claim("claim-1"))
    controller.submit_coordination_decision(
        "team/project",
        first.claim.claim_id,
        Decision(ClaimState.APPROVED, "first claim may run"),
    )
    controller.start_execution("team/project", first.claim.claim_id)
    second = controller.submit_claim(make_claim("claim-2"))
    assert second.state is ClaimState.WAITING

    async def release_waiter() -> None:
        await asyncio.sleep(0.05)
        controller.complete_claim("team/project", first.claim.claim_id)

    release_task = asyncio.create_task(release_waiter())
    _, result = await mcp.call_tool(
        "wait_for_decision",
        {
            "project_id": "team/project",
            "claim_id": second.claim.claim_id,
            "timeout_seconds": 1,
            "poll_seconds": 0.01,
        },
    )
    await release_task

    assert result["state"] == "approved_with_constraints"
    assert result["allowed_actions"] == ["start_execution"]


@pytest.mark.anyio
async def test_integrate_claim_marks_completed_work_integrated() -> None:
    mcp, controller = make_mcp()
    claim = controller.submit_claim(make_claim("claim-1"))
    controller.submit_coordination_decision(
        claim.claim.project_id,
        claim.claim.claim_id,
        Decision(ClaimState.APPROVED, "claim may run"),
    )
    controller.start_execution(claim.claim.project_id, claim.claim.claim_id)
    controller.complete_claim(claim.claim.project_id, claim.claim.claim_id)

    _, result = await mcp.call_tool(
        "integrate_claim",
        {"project_id": "team/project", "claim_id": "claim-1"},
    )

    assert result["state"] == "integrated"
