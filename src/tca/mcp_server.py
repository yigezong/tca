from __future__ import annotations

import asyncio
import os
import time

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from .controller import Controller
from .domain import ClaimState, Decision
from .rules import BasicRuleChecker
from .store import build_store

INSTRUCTIONS = """
TCA coordinates coding-agent work in Git-enabled projects.
A: after publishing a Claim branch, call wait_for_decision before shared code changes.
A: only start when allowed_actions includes start_execution; call complete_claim when done.
M: use get_coordination_task and submit_coordination_decision.
The Controller is authoritative; A and M must not infer state from local memory.
""".strip()


def build_controller() -> Controller:
    return Controller(
        build_store(),
        BasicRuleChecker(),
    )


def create_mcp(controller: Controller | None = None) -> FastMCP:
    controller = controller or build_controller()
    allowed_hosts = [
        host.strip()
        for host in os.getenv(
            "TCA_ALLOWED_HOSTS", "127.0.0.1,localhost"
        ).split(",")
        if host.strip()
    ]
    mcp = FastMCP(
        "Team Code Agent",
        instructions=INSTRUCTIONS,
        host="127.0.0.1",
        port=8000,
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(allowed_hosts=allowed_hosts),
    )

    @mcp.tool()
    def get_claim_state(claim_id: str) -> dict[str, object]:
        """For A: get authoritative state, constraints, and allowed actions."""
        return controller.get_claim_state(claim_id)

    @mcp.tool()
    async def wait_for_decision(
        claim_id: str, timeout_seconds: float = 30.0, poll_seconds: float = 0.25
    ) -> dict[str, object]:
        """For A: wait until C finishes assessing a Claim or timeout expires."""
        deadline = time.monotonic() + max(0.0, timeout_seconds)
        pending = {ClaimState.RECEIVED.value, ClaimState.ASSESSING.value}
        while True:
            result = controller.get_claim_state(claim_id)
            if result["state"] not in pending or time.monotonic() >= deadline:
                return result
            await asyncio.sleep(max(0.05, poll_seconds))

    @mcp.tool()
    def start_execution(claim_id: str) -> dict[str, object]:
        """For A: move an approved Claim to executing before shared changes."""
        return controller.start_execution(claim_id).to_mapping()

    @mcp.tool()
    def complete_claim(claim_id: str) -> dict[str, object]:
        """For A: complete executing work and trigger dependent reevaluation."""
        return controller.complete_claim(claim_id).to_mapping()

    @mcp.tool()
    def get_coordination_task(project_id: str = "") -> dict[str, object] | None:
        """For M: get the oldest Claim awaiting semantic coordination."""
        task = controller.get_coordination_task(project_id or None)
        return task.to_mapping() if task else None

    @mcp.tool()
    def submit_coordination_decision(
        claim_id: str,
        state: str,
        reason: str,
        constraints: list[str] | None = None,
        wait_for: list[str] | None = None,
        conflicts_with: list[str] | None = None,
    ) -> dict[str, object]:
        """For M: submit a recommendation; C validates and applies it."""
        decision = Decision.from_mapping(
            {
                "state": state,
                "reason": reason,
                "constraints": constraints or [],
                "wait_for": wait_for or [],
                "conflicts_with": conflicts_with or [],
            }
        )
        return controller.submit_coordination_decision(claim_id, decision).to_mapping()

    return mcp
