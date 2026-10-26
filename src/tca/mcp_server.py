from __future__ import annotations

import asyncio
import os
import time

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from .controller import Controller
from .domain import ACTIVE_STATES, Claim, ClaimState, CoordinationAdvice, Decision, Handoff, SubgoalEvent
from .rules import BasicRuleChecker
from .store import build_store
from .project_access import ProjectAccess

INSTRUCTIONS = """
TCA coordinates coding-agent work in Git-enabled projects.
A: after publishing or submitting a Claim, call wait_for_decision before shared code changes.
A: only start when allowed_actions includes start_execution; call complete_claim when done.
A: while executing, call report_progress to keep the claim alive and show progress;
   when a successor needs an interface, publish_handoff with a commit, exported
   symbols, and behavior contracts. This does not mark the publishing claim done.
   C auto-completes claims whose execution deadline elapses without complete_claim,
   and auto-resolves M decisions that stay pending too long.
M: use wait_for_coordination_task to observe public project state, then publish
non-blocking natural-language coordination advice. It must not centrally split
A's feature into technical tasks or block work already approved by C. P is a
human reviewer: P1/P2 each give only their paired developer natural-language
feedback and never inspect both patches or edit code. completed_claims are
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


def create_mcp(controller: Controller | None = None, access: ProjectAccess | None = None) -> FastMCP:
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
        parsed = Claim.from_mapping(claim)
        if access:
            access.require(parsed.project_id, "a", parsed.author)
            reserved = controller.store.reserve_agent_claim(
                parsed.project_id, parsed.author.lower(), parsed.claim_id
            )
            try:
                return controller.submit_claim(parsed).to_mapping()
            except Exception:
                if reserved:
                    try:
                        remaining = controller.store.get(parsed.project_id, parsed.claim_id)
                    except KeyError:
                        remaining = None
                    if remaining is None or remaining.state not in ACTIVE_STATES:
                        controller.store.release_agent_claim(
                            parsed.project_id, parsed.author.lower(), parsed.claim_id
                        )
                raise
        return controller.submit_claim(parsed).to_mapping()

    @mcp.tool()
    def get_claim_state(project_id: str, claim_id: str) -> dict[str, object]:
        """For A: get authoritative state, constraints, and allowed actions."""
        if access:
            access.role(project_id)
        return controller.get_claim_state(project_id, claim_id)

    @mcp.tool()
    async def wait_for_decision(
        project_id: str,
        claim_id: str,
        timeout_seconds: float = 30.0,
        poll_seconds: float = 0.25,
    ) -> dict[str, object]:
        """For A: wait until C finishes assessing a Claim or timeout expires."""
        if access:
            await asyncio.to_thread(access.role, project_id)
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
        if access:
            access.require_claim_owner(project_id, claim_id)
        return controller.start_execution(project_id, claim_id).to_mapping()

    @mcp.tool()
    def complete_claim(project_id: str, claim_id: str) -> dict[str, object]:
        """For A: mark a pushed work branch completed and trigger reevaluation."""
        if access:
            access.require_claim_owner(project_id, claim_id)
        return controller.complete_claim(project_id, claim_id).to_mapping()

    @mcp.tool()
    def report_progress(
        project_id: str, claim_id: str, progress: str = ""
    ) -> dict[str, object]:
        """For A: report progress/heartbeat while executing; extends the execution deadline."""
        if access:
            access.require_claim_owner(project_id, claim_id)
        return controller.report_progress(project_id, claim_id, progress).to_mapping()

    @mcp.tool()
    def request_requirements_clarification(
        project_id: str,
        claim_id: str,
        question: str,
        context: str = "",
    ) -> dict[str, object]:
        """For A: ask its P only when product requirements are ambiguous.

        Do not use this for merge conflicts, rebases, tests, or implementation
        uncertainty: the later integrator resolves those from the latest code.
        Context is natural language only; never include a patch.
        """
        if access:
            access.require_claim_owner(project_id, claim_id)
        return controller.request_requirements_clarification(
            project_id, claim_id, question, context
        ).to_mapping()

    @mcp.tool()
    def record_subgoal_event(event: dict[str, object]) -> dict[str, object]:
        """For A: report one coarse subgoal start/finish/integration event."""
        parsed = SubgoalEvent.from_mapping(event)
        if access:
            access.require_claim_owner(parsed.project_id, parsed.claim_id)
            access.require(parsed.project_id, "a", parsed.author)
        return controller.record_subgoal_event(parsed).to_mapping()

    @mcp.tool()
    def get_integration_context(project_id: str, claim_id: str) -> dict[str, object]:
        """For the later A: obtain public related work and M advice before integrating."""
        if access:
            access.role(project_id)
        return controller.get_integration_context(project_id, claim_id)

    @mcp.tool()
    def publish_coordination_advice(advice: dict[str, object]) -> dict[str, object]:
        """For M: publish non-blocking natural-language advice to one owning A."""
        parsed = CoordinationAdvice.from_mapping(advice)
        if access:
            access.require(parsed.project_id, "m")
            if parsed.advisor != "agent-m":
                raise PermissionError("M advice must use advisor agent-m")
        return controller.publish_coordination_advice(parsed).to_mapping()

    @mcp.tool()
    def publish_handoff(
        project_id: str, claim_id: str, handoff: dict[str, object]
    ) -> dict[str, object]:
        """For A: publish one immutable interface checkpoint while executing.

        A successor declared with dependency_mode=after_interface is released
        after this checkpoint, rather than after the publisher completes.
        """
        if access:
            access.require_claim_owner(project_id, claim_id)
        return controller.publish_handoff(
            project_id, claim_id, Handoff.from_mapping(handoff)
        ).to_mapping()

    @mcp.tool()
    def integrate_claim(project_id: str, claim_id: str) -> dict[str, object]:
        """Fallback: confirm integration when no webhook can prove branch ancestry."""
        if access:
            access.require(project_id, "m")
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
        if access:
            if not project_id:
                raise PermissionError("M must select a project")
            await asyncio.to_thread(access.require, project_id, "m")
            if worker_id != "agent-m":
                raise PermissionError("M worker_id must be agent-m")
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
        boundary_contract: str = "",
        worker_id: str | None = None,
    ) -> dict[str, object]:
        """For M: submit a recommendation; C validates and applies it."""
        if access:
            access.require(project_id, "m")
            if worker_id not in (None, "agent-m"):
                raise PermissionError("M worker_id must be agent-m")
        decision = Decision.from_mapping(
            {
                "state": state,
                "reason": reason,
                "constraints": constraints or [],
                "wait_for": wait_for or [],
                "conflicts_with": conflicts_with or [],
                "uncertainty": uncertainty,
                "boundary_contract": boundary_contract,
            }
        )
        return controller.submit_coordination_decision(
            project_id, claim_id, decision, worker_id=worker_id
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
    ) -> dict[str, object]:
        """For a human P: record natural-language feedback for one owning A.

        Do not send any patch through MCP. P1/P2 each see only their paired
        developer's patch; the server persists only minimal requirements for
        that developer. uncertainty is 0 (certain) through 1 (no preference).
        """
        if access:
            access.require(project_id, "m")
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
        return controller.resolve_privileged_review(
            project_id, claim_id, decision
        ).to_mapping()

    return mcp
