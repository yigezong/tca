from __future__ import annotations

from .coordinator import Coordinator
from .domain import Claim, ClaimRecord, ClaimState, allowed_actions
from .store import ClaimStore


class Controller:
    """Deterministic state authority shared by webhook and future MCP transports."""

    def __init__(self, store: ClaimStore, coordinator: Coordinator) -> None:
        self.store = store
        self.coordinator = coordinator

    def submit_claim(self, claim: Claim) -> ClaimRecord:
        record = self.store.save_received(claim)
        self.store.transition(
            claim.claim_id, {ClaimState.RECEIVED}, ClaimState.ASSESSING
        )
        active = self.store.list_active(claim.project_id, exclude=claim.claim_id)
        decision = self.coordinator.assess(claim, active)
        return self.store.transition(
            claim.claim_id,
            {ClaimState.ASSESSING},
            decision.state,
            decision=decision,
        )

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
            raise ValueError(f"claim {claim.claim_id} cannot be amended from {current.state.value}")
        return self.submit_claim(claim)

    def heartbeat(self, claim_id: str) -> ClaimRecord:
        return self.store.record_activity(claim_id, "heartbeat")

    def update_progress(self, claim_id: str, status: str, message: str = "") -> ClaimRecord:
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

    def _reevaluate_waiters(self, completed: ClaimRecord) -> None:
        active = self.store.list_active(completed.claim.project_id, exclude=completed.claim.claim_id)
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
            decision = self.coordinator.assess(record.claim, others)
            self.store.transition(
                record.claim.claim_id,
                {ClaimState.ASSESSING},
                decision.state,
                decision=decision,
                detail={"triggered_by": completed.claim.claim_id},
            )
