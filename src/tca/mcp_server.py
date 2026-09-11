from __future__ import annotations

import asyncio
import os
import time

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from .controller import Controller
from .domain import Claim, ClaimState, Decision
from .rules import BasicRuleChecker
from .store import build_store

INSTRUCTIONS = """
TCA coordinates coding-agent work in Git-enabled projects.
A: after publishing or submitting a Claim, call wait_for_decision before shared code changes.
A: only start when allowed_actions includes start_execution; call complete_claim when done.
A: while executing, call report_progress to keep the claim alive and show progress;
   C auto-completes claims whose execution deadline elapses without complete_claim,
   and auto-resolves M decisions that stay pending too long.
M: use wait_for_coordination_task, then submit_coordination_decision. An owning A,
not M, requests requirements alignment when its declared contracts are insufficient.
The alignment reviewer resolves that request from its private, local context. completed_claims are
pending integration; overlapping work must incorporate their work branches
or require a human decision.
GitHub default-branch pushes automatically confirm integration. integrate_claim is only
a fallback for providers or merge strategies that cannot preserve branch ancestry.
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
    def submit_claim(claim: dict[str, object]) -> dict[str, object]:
        """For A/adapters: submit a Claim directly when no Git provider webhook exists."""
        return controller.submit_claim(Claim.from_mapping(claim)).to_mapping()

    @mcp.tool()
    def get_claim_state(project_id: str, claim_id: str) -> dict[str, object]:
        """For A: get authoritative state, constraints, and allowed actions."""
        return controller.get_claim_state(project_id, claim_id)

    @mcp.tool()
    async def wait_for_decision(
        project_id: str,
        claim_id: str,
        timeout_seconds: float = 30.0,
        poll_seconds: float = 0.25,
    ) -> dict[str, object]:
        """For A: wait until C finishes assessing a Claim or timeout expires."""
        deadline = time.monotonic() + max(0.0, timeout_seconds)
        pending = {
            ClaimState.RECEIVED.value,
            ClaimState.ASSESSING.value,
            ClaimState.WAITING.value,
        }
        while True:
            result = controller.get_claim_state(project_id, claim_id)
            if result["state"] not in pending or time.monotonic() >= deadline:
                return result
            await asyncio.sleep(max(0.05, poll_seconds))

    @mcp.tool()
    def start_execution(project_id: str, claim_id: str) -> dict[str, object]:
        """For A: move an approved Claim to executing before shared changes."""
        return controller.start_execution(project_id, claim_id).to_mapping()

    @mcp.tool()
    def complete_claim(project_id: str, claim_id: str) -> dict[str, object]:
        """For A: mark a pushed work branch completed and trigger reevaluation."""
        return controller.complete_claim(project_id, claim_id).to_mapping()

    @mcp.tool()
    def report_progress(
        project_id: str, claim_id: str, progress: str = ""
    ) -> dict[str, object]:
        """For A: report progress/heartbeat while executing; extends the execution deadline."""
        return controller.report_progress(project_id, claim_id, progress).to_mapping()

    @mcp.tool()
    def integrate_claim(project_id: str, claim_id: str) -> dict[str, object]:
        """Fallback: confirm integration when no webhook can prove branch ancestry."""
        return controller.integrate_claim(project_id, claim_id).to_mapping()

    @mcp.tool()
    async def wait_for_coordination_task(
        project_id: str = "",
        timeout_seconds: float = 30.0,
        poll_seconds: float = 0.25,
        worker_id: str = "agent-m",
        lease_seconds: float = 300.0,
    ) -> dict[str, object] | None:
        """For M: lease a task plus context; expired leases are automatically retried."""
        deadline = time.monotonic() + max(0.0, timeout_seconds)
        while True:
            task = controller.lease_coordination_task(
                project_id or None, worker_id, lease_seconds
            )
            if task is not None or time.monotonic() >= deadline:
                return task.to_mapping() if task else None
            await asyncio.sleep(max(0.05, poll_seconds))

    @mcp.tool()
    def submit_coordination_decision(
        project_id: str,
        claim_id: str,
        state: str,
        reason: str,
        constraints: list[str] | None = None,
        wait_for: list[str] | None = None,
        conflicts_with: list[str] | None = None,
        uncertainty: float | None = None,
        worker_id: str | None = None,
    ) -> dict[str, object]:
        """For M: submit a recommendation; C validates and applies it."""
        decision = Decision.from_mapping(
            {
                "state": state,
                "reason": reason,
                "constraints": constraints or [],
                "wait_for": wait_for or [],
                "conflicts_with": conflicts_with or [],
                "uncertainty": uncertainty,
            }
        )
        return controller.submit_coordination_decision(
            project_id, claim_id, decision, worker_id=worker_id
        ).to_mapping()

    @mcp.tool()
    def request_requirements_alignment(
        project_id: str, claim_id: str, requester: str, reason: str
    ) -> dict[str, object]:
        """For an owning A: request requirement, interface, or acceptance alignment."""
        return controller.request_requirements_alignment(
            project_id, claim_id, requester, reason
        ).to_mapping()

    @mcp.tool()
    def resolve_privileged_review(
        project_id: str,
        claim_id: str,
        state: str,
        reason: str,
        constraints: list[str] | None = None,
        wait_for: list[str] | None = None,
        conflicts_with: list[str] | None = None,
        uncertainty: float | None = None,
        alignment_owner: str = "",
        alignment_compatibility: list[str] | None = None,
        alignment_acceptance: list[str] | None = None,
        alignment_split: list[str] | None = None,
    ) -> dict[str, object]:
        """Resolve an A-owned requirements-alignment request from private context.

        Do not send combined.patch through MCP. P keeps that context in its
        isolated worktree and submits only minimal requirements for the owning A.
        uncertainty is 0 (certain) through 1 (no reliable preference).
        """
        decision = Decision.from_mapping(
            {
                "state": state,
                "reason": reason,
                "constraints": constraints or [],
                "wait_for": wait_for or [],
                "conflicts_with": conflicts_with or [],
                "uncertainty": uncertainty,
                "alignment_owner": alignment_owner,
                "alignment_compatibility": alignment_compatibility or [],
                "alignment_acceptance": alignment_acceptance or [],
                "alignment_split": alignment_split or [],
            }
        )
        return controller.resolve_privileged_review(
            project_id, claim_id, decision
        ).to_mapping()

    return mcp
