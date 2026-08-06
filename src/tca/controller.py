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
from .store_protocol import Store


class Controller:
    """Deterministic state authority shared by webhook and MCP transports."""

    def __init__(
        self,
        store: Store,
        rule_checker: BasicRuleChecker,
    ) -> None:
        self.store = store
        self.rule_checker = rule_checker

    def submit_claim(self, claim: Claim) -> ClaimRecord:
        self.store.save_received(claim)
        self.store.transition(
            claim.project_id,
            claim.claim_id,
            {ClaimState.RECEIVED},
            ClaimState.ASSESSING,
        )
        active = self.store.list_active(claim.project_id, exclude=claim.claim_id)
        return self._assess_or_queue(claim, active)

    def get_claim_state(self, project_id: str, claim_id: str) -> dict[str, object]:
        record = self.store.get(project_id, claim_id)
        result = record.to_mapping()
        result["allowed_actions"] = list(allowed_actions(record.state))
        return result

    def start_execution(self, project_id: str, claim_id: str) -> ClaimRecord:
        return self.store.transition(
            project_id,
            claim_id,
            {ClaimState.APPROVED, ClaimState.APPROVED_WITH_CONSTRAINTS},
            ClaimState.EXECUTING,
        )

    def complete_claim(self, project_id: str, claim_id: str) -> ClaimRecord:
        completed = self.store.transition(
            project_id, claim_id, {ClaimState.EXECUTING}, ClaimState.COMPLETED
        )
        self._reevaluate_waiters(completed)
        return completed

    def get_coordination_task(
        self, project_id: str | None = None
    ) -> CoordinationTask | None:
        claim_key = self.store.next_coordination_claim(project_id)
        if claim_key is None:
            return None
        task_project_id, claim_id = claim_key
        record = self.store.get(task_project_id, claim_id)
        if record.state is not ClaimState.ASSESSING:
            raise ValueError(
                f"claim {task_project_id}:{claim_id} is no longer awaiting coordination"
            )
        active = self.store.list_active(
            record.claim.project_id, exclude=record.claim.claim_id
        )
        return CoordinationTask(record, tuple(active))

    def submit_coordination_decision(
        self, project_id: str, claim_id: str, decision: Decision
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
            project_id,
            claim_id,
            {ClaimState.ASSESSING},
            decision.state,
            decision=decision,
        )
        self.store.complete_coordination(project_id, claim_id)
        return record

    def _assess_or_queue(
        self, claim: Claim, active: list[ClaimRecord]
    ) -> ClaimRecord:
        hard_decision = self.rule_checker.check(claim, active)
        if hard_decision is not None:
            return self.store.transition(
                claim.project_id,
                claim.claim_id,
                {ClaimState.ASSESSING},
                hard_decision.state,
                decision=hard_decision,
            )
        self.store.queue_coordination(claim.project_id, claim.claim_id)
        return self.store.get(claim.project_id, claim.claim_id)

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
                record.claim.project_id,
                record.claim.claim_id,
                {ClaimState.WAITING},
                ClaimState.ASSESSING,
            )
            others = self.store.list_active(
                record.claim.project_id, exclude=record.claim.claim_id
            )
            self._assess_or_queue(record.claim, others)
