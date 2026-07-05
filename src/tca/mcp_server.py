from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .controller import Controller
from .coordinator import RuleBasedCoordinator
from .domain import ClaimState
from .store import ClaimStore

INSTRUCTIONS = """
TCA coordinates coding-agent work in Git-enabled projects.
After publishing a Claim branch, call wait_for_decision before shared code changes.
Only start when allowed_actions includes start_execution.
Send heartbeat and progress while executing, and call complete_claim when done.
The Controller is authoritative; do not infer state from local memory.
""".strip()


def build_controller() -> Controller:
    database = Path(os.getenv("TCA_DATABASE", "tca.db"))
    return Controller(ClaimStore(database), RuleBasedCoordinator())


def create_mcp(controller: Controller | None = None) -> FastMCP:
    controller = controller or build_controller()
    mcp = FastMCP(
        "Team Code Agent",
        instructions=INSTRUCTIONS,
        host=os.getenv("TCA_HOST", "127.0.0.1"),
        port=int(os.getenv("TCA_MCP_PORT", "8000")),
        stateless_http=True,
        json_response=True,
    )

    @mcp.tool()
    def get_claim_state(claim_id: str) -> dict[str, object]:
        """Get authoritative Claim state, decision, constraints, and allowed actions."""
        return controller.get_claim_state(claim_id)

    @mcp.tool()
    async def wait_for_decision(
        claim_id: str, timeout_seconds: float = 30.0, poll_seconds: float = 0.25
    ) -> dict[str, object]:
        """Wait until C finishes assessing a Claim, or return when the timeout expires."""
        deadline = time.monotonic() + max(0.0, timeout_seconds)
        pending = {ClaimState.RECEIVED.value, ClaimState.ASSESSING.value}
        while True:
            result = controller.get_claim_state(claim_id)
            if result["state"] not in pending or time.monotonic() >= deadline:
                return result
            await asyncio.sleep(max(0.05, poll_seconds))

    @mcp.tool()
    def start_execution(claim_id: str) -> dict[str, object]:
        """Move an approved Claim into executing state before shared code changes."""
        return controller.start_execution(claim_id).to_mapping()

    @mcp.tool()
    def heartbeat(claim_id: str) -> dict[str, object]:
        """Record that work on an active Claim is still alive."""
        return controller.heartbeat(claim_id).to_mapping()

    @mcp.tool()
    def update_progress(claim_id: str, status: str, message: str = "") -> dict[str, object]:
        """Record executing, waiting, or blocked progress without changing Claim scope."""
        return controller.update_progress(claim_id, status, message).to_mapping()

    @mcp.tool()
    def complete_claim(claim_id: str) -> dict[str, object]:
        """Complete an executing Claim and trigger reevaluation of dependent work."""
        return controller.complete_claim(claim_id).to_mapping()

    return mcp


def main() -> None:
    create_mcp().run(transport="streamable-http")


if __name__ == "__main__":
    main()
