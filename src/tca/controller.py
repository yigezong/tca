from __future__ import annotations

from .domain import (
    Claim,
    ClaimRecord,
    ClaimState,
    CoordinationTask,
    Decision,
    allowed_actions,
)
from .rules import BasicRuleChecker
from .store import ClaimStore


class Controller:
    """Deterministic state authority shared by webhook and MCP transports."""

    def __init__(
        self,
        store: ClaimStore,
        rule_checker: BasicRuleChecker,
        *,
        require_external_coordination: bool = False,
    ) -> None:
        self.store = store
        self.rule_checker = rule_checker
        self.require_external_coordination = require_external_coordination

    def submit_claim(self, claim: Claim) -> ClaimRecord:
        self.store.save_received(claim)
        self.store.transition(
            claim.claim_id, {ClaimState.RECEIVED}, ClaimState.ASSESSING
        )
        active = self.store.list_active(claim.project_id, exclude=claim.claim_id)
        return self._assess_or_queue(claim, active)

    def get_claim_state(self, claim_id: str) -> dict[str, object]:
        record = self.store.get(claim_id)
        result = record.to_mapping()
        result["allowed_actions"] = list(allowed_actions(record.state))
        return result

    def start_execution(self, claim_id: str) -> ClaimRecord:
        return self.store.transition(
            claim_id,
            {ClaimState.APPROVED, ClaimState.APPROVED_WITH_CONSTRAINTS},
            ClaimState.EXECUTING,
        )

    def amend_claim(self, claim: Claim) -> ClaimRecord:
        current = self.store.get(claim.claim_id)
        if current.state not in {
            ClaimState.APPROVED,
            ClaimState.APPROVED_WITH_CONSTRAINTS,
            ClaimState.WAITING,
            ClaimState.NEEDS_HUMAN,
            ClaimState.BLOCKED,
            ClaimState.EXECUTING,
            ClaimState.STALE,
        }:
            raise ValueError(
                f"claim {claim.claim_id} cannot be amended from {current.state.value}"
            )
        return self.submit_claim(claim)

    def heartbeat(self, claim_id: str) -> ClaimRecord:
        return self.store.record_activity(claim_id, "heartbeat")

    def update_progress(
        self, claim_id: str, status: str, message: str = ""
    ) -> ClaimRecord:
        if not status.strip():
            raise ValueError("progress status is required")
        return self.store.record_activity(
            claim_id, "progress_updated", {"status": status, "message": message}
        )

    def complete_claim(self, claim_id: str) -> ClaimRecord:
        completed = self.store.transition(
            claim_id, {ClaimState.EXECUTING}, ClaimState.COMPLETED
        )
        self._reevaluate_waiters(completed)
        return completed

    def get_coordination_task(
        self, project_id: str | None = None
    ) -> CoordinationTask | None:
        claim_id = self.store.next_coordination_claim_id(project_id)
        if claim_id is None:
            return None
        record = self.store.get(claim_id)
        if record.state is not ClaimState.ASSESSING:
            raise ValueError(f"claim {claim_id} is no longer awaiting coordination")
        active = self.store.list_active(
            record.claim.project_id, exclude=record.claim.claim_id
        )
        return CoordinationTask(record, tuple(active))

    def submit_coordination_decision(
        self, claim_id: str, decision: Decision, coordinator_id: str
    ) -> ClaimRecord:
        allowed_states = {
            ClaimState.APPROVED,
            ClaimState.APPROVED_WITH_CONSTRAINTS,
            ClaimState.WAITING,
            ClaimState.NEEDS_HUMAN,
            ClaimState.BLOCKED,
        }
        if decision.state not in allowed_states:
            raise ValueError(f"M cannot move a Claim to {decision.state.value}")
        record = self.store.transition(
            claim_id,
            {ClaimState.ASSESSING},
            decision.state,
            decision=decision,
            detail={"coordinator_id": coordinator_id},
        )
        self.store.complete_coordination(claim_id, coordinator_id)
        return record

    def _assess_or_queue(
        self, claim: Claim, active: list[ClaimRecord]
    ) -> ClaimRecord:
        hard_decision = self.rule_checker.check(claim, active)
        if hard_decision is not None:
            return self.store.transition(
                claim.claim_id,
                {ClaimState.ASSESSING},
                hard_decision.state,
                decision=hard_decision,
            )
        if self.require_external_coordination:
            self.store.queue_coordination(claim.claim_id)
            return self.store.get(claim.claim_id)
        decision = Decision(
            state=ClaimState.APPROVED,
            reason="basic rules found no active dependency or path conflict",
        )
        return self.store.transition(
            claim.claim_id,
            {ClaimState.ASSESSING},
            decision.state,
            decision=decision,
        )

    def _reevaluate_waiters(self, completed: ClaimRecord) -> None:
        active = self.store.list_active(
            completed.claim.project_id, exclude=completed.claim.claim_id
        )
        for record in active:
            if record.state is not ClaimState.WAITING or record.decision is None:
                continue
            if completed.claim.claim_id not in record.decision.wait_for:
                continue
            self.store.transition(
                record.claim.claim_id, {ClaimState.WAITING}, ClaimState.ASSESSING
            )
            others = self.store.list_active(
                record.claim.project_id, exclude=record.claim.claim_id
            )
            reassessed = self._assess_or_queue(record.claim, others)
            self.store.record_activity(
                reassessed.claim.claim_id,
                "waiter_reassessed",
                {"triggered_by": completed.claim.claim_id},
            )
