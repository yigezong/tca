import pytest

from tca.controller import Controller
from tca.domain import Claim
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
        work_branch=f"feature/{claim_id}",
        goal="make a coordinated change",
        planned_paths=("src/example/**",),
    )


@pytest.mark.anyio
async def test_mcp_exposes_wait_for_coordination_task() -> None:
    mcp, _ = make_mcp()

    tools = await mcp.list_tools()

    assert "wait_for_coordination_task" in {tool.name for tool in tools}


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
