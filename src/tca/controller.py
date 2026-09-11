from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

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
        *,
        execution_timeout: float = 3600.0,
        heartbeat_timeout: float = 900.0,
        m_decision_timeout: float = 600.0,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = store
        self.rule_checker = rule_checker
        self.execution_timeout = execution_timeout
        self.heartbeat_timeout = heartbeat_timeout
        self.m_decision_timeout = m_decision_timeout
        self._clock = clock or (lambda: datetime.now(UTC))

    def _now_epoch(self) -> float:
        return self._clock().timestamp()

    def _now_iso(self) -> str:
        return self._clock().isoformat()

    @staticmethod
    def _parse_epoch(value: str) -> float:
        if not value:
            return float("-inf")
        try:
            return datetime.fromisoformat(value).timestamp()
        except ValueError:
            return float("-inf")

    def run_deadline_checks(self, project_id: str | None = None) -> None:
        """Deterministic timeout resolution for stuck claims.

        Called lazily at the top of every stateful operation: C has no
        background scheduler, so any interaction with the system also
        advances stalled claims. All outcomes are decided here by C and
        only by C.
        """
        records = (
            self.store.list_active(project_id)
            if project_id is not None
            else self.store.list_active_all()
        )
        for record in records:
            if record.state is ClaimState.EXECUTING:
                self._check_execution_deadline(record)
            elif record.state is ClaimState.ASSESSING:
                self._check_m_decision_deadline(record)

    def _check_execution_deadline(self, record: ClaimRecord) -> None:
        if not record.executing_since:
            return
        deadline = self._parse_epoch(record.executing_since) + self.execution_timeout
        if record.last_heartbeat:
            deadline = max(
                deadline,
                self._parse_epoch(record.last_heartbeat) + self.heartbeat_timeout,
            )
        if self._now_epoch() < deadline:
            return
        decision = Decision(
            state=ClaimState.COMPLETED,
            reason=(
                f"execution deadline ({self.execution_timeout:g}s) elapsed "
                "without complete_claim; auto-completed by C"
            ),
        )
        completed = self.store.transition(
            record.claim.project_id,
            record.claim.claim_id,
            {ClaimState.EXECUTING},
            ClaimState.COMPLETED,
            decision=decision,
        )
        self._reevaluate_waiters(completed)

    def _check_m_decision_deadline(self, record: ClaimRecord) -> None:
        created_at = self.store.coordination_created_at(
            record.claim.project_id, record.claim.claim_id
        )
        if not created_at:
            return
        if self._now_epoch() < self._parse_epoch(created_at) + self.m_decision_timeout:
            return
        active = self.store.list_active(
            record.claim.project_id, exclude=record.claim.claim_id
        )
        decision = self.rule_checker.check(record.claim, active)
        if decision is None:
            decision = Decision(
                state=ClaimState.APPROVED,
                reason=(
                    "M decision deadline elapsed; rule checker found no conflict; "
                    "auto-approved by C"
                ),
            )
        if (
            decision.state is ClaimState.WAITING
            and decision.wait_for
            and self._creates_wait_cycle(
                record.claim.claim_id, decision.wait_for, active
            )
        ):
            decision = Decision(
                state=ClaimState.NEEDS_HUMAN,
                reason=(
                    "M decision deadline elapsed; deterministic check found a "
                    f"wait cycle involving {', '.join(decision.wait_for)}; "
                    "needs a human decision"
                ),
            )
        self.store.transition(
            record.claim.project_id,
            record.claim.claim_id,
            {ClaimState.ASSESSING},
            decision.state,
            decision=decision,
        )
        self.store.complete_coordination(
            record.claim.project_id, record.claim.claim_id
        )

    @staticmethod
    def _creates_wait_cycle(
        claim_id: str,
        wait_for: tuple[str, ...],
        records: list[ClaimRecord],
    ) -> bool:
        adjacency = {
            record.claim.claim_id: list(record.decision.wait_for)
            for record in records
            if record.decision and record.decision.wait_for
        }
        adjacency[claim_id] = list(wait_for)

        def reachable(start: str, target: str) -> bool:
            seen: set[str] = set()
            stack = [start]
            while stack:
                node = stack.pop()
                if node == target:
                    return True
                if node in seen:
                    continue
                seen.add(node)
                stack.extend(adjacency.get(node, ()))
            return False

        return any(reachable(target, claim_id) for target in wait_for)

    def submit_claim(self, claim: Claim) -> ClaimRecord:
        expected_work_branch = f"feature/{claim.author}/{claim.claim_id}"
        if claim.work_branch != expected_work_branch:
            raise ValueError(f"work_branch must be {expected_work_branch}")

        self.run_deadline_checks(claim.project_id)
        try:
            existing = self.store.get(claim.project_id, claim.claim_id)
        except KeyError:
            existing = None
        if existing is not None:
            immutable_fields = ("author", "base_commit", "work_branch")
            changed = [
                field
                for field in immutable_fields
                if getattr(existing.claim, field) != getattr(claim, field)
            ]
            if changed:
                raise ValueError(
                    "claim revisions cannot change: " + ", ".join(changed)
                )
            if claim == existing.claim:
                return existing

        self.store.save_received(claim)
        self.store.transition(
            claim.project_id,
            claim.claim_id,
            {ClaimState.RECEIVED},
            ClaimState.ASSESSING,
        )
        context = self.store.list_coordination_context(
            claim.project_id, exclude=claim.claim_id
        )
        return self._assess_or_queue(claim, context)

    def get_claim_state(self, project_id: str, claim_id: str) -> dict[str, object]:
        self.run_deadline_checks(project_id)
        record = self.store.get(project_id, claim_id)
        result = record.to_mapping()
        result["allowed_actions"] = list(allowed_actions(record.state))
        return result

    def start_execution(self, project_id: str, claim_id: str) -> ClaimRecord:
        self.run_deadline_checks(project_id)
        record = self.store.get(project_id, claim_id)
        if record.state is ClaimState.EXECUTING:
            return record
        if record.state not in {
            ClaimState.APPROVED,
            ClaimState.APPROVED_WITH_CONSTRAINTS,
        }:
            raise ValueError(
                f"illegal transition for {claim_id}: {record.state.value} -> executing"
            )
        record = self.store.transition(
            project_id,
            claim_id,
            {ClaimState.APPROVED, ClaimState.APPROVED_WITH_CONSTRAINTS},
            ClaimState.EXECUTING,
        )
        self.store.record_execution_start(project_id, claim_id, self._now_iso())
        return record

    def complete_claim(self, project_id: str, claim_id: str) -> ClaimRecord:
        self.run_deadline_checks(project_id)
        record = self.store.get(project_id, claim_id)
        if record.state in {ClaimState.COMPLETED, ClaimState.INTEGRATED}:
            return record
        if record.state is not ClaimState.EXECUTING:
            raise ValueError(
                f"illegal transition for {claim_id}: {record.state.value} -> completed"
            )
        completed = self.store.transition(
            project_id, claim_id, {ClaimState.EXECUTING}, ClaimState.COMPLETED
        )
        self._reevaluate_waiters(completed)
        return completed

    def report_progress(
        self, project_id: str, claim_id: str, progress: str = ""
    ) -> ClaimRecord:
        """A-side heartbeat: keeps an executing claim alive past its deadline."""
        self.run_deadline_checks(project_id)
        record = self.store.get(project_id, claim_id)
        if record.state is ClaimState.EXECUTING:
            self.store.update_progress(
                project_id, claim_id, progress, self._now_iso()
            )
            record = self.store.get(project_id, claim_id)
        return record

    def integrate_claim(self, project_id: str, claim_id: str) -> ClaimRecord:
        self.run_deadline_checks(project_id)
        integrated = self.store.transition(
            project_id,
            claim_id,
            {ClaimState.COMPLETED},
            ClaimState.INTEGRATED,
        )
        self._reevaluate_waiters(integrated)
        return integrated

    def list_completed_claims(self, project_id: str) -> tuple[ClaimRecord, ...]:
        self.run_deadline_checks(project_id)
        return tuple(
            record
            for record in self.store.list_coordination_context(project_id)
            if record.state is ClaimState.COMPLETED
        )

    def get_coordination_task(
        self, project_id: str | None = None
    ) -> CoordinationTask | None:
        self.run_deadline_checks(project_id)
        claim_key = self.store.next_coordination_claim(project_id)
        if claim_key is None:
            return None
        task_project_id, claim_id = claim_key
        record = self.store.get(task_project_id, claim_id)
        if record.state is not ClaimState.ASSESSING:
            raise ValueError(
                f"claim {task_project_id}:{claim_id} is no longer awaiting coordination"
            )
        context = self.store.list_coordination_context(
            record.claim.project_id, exclude=record.claim.claim_id
        )
        active = tuple(
            item for item in context if item.state is not ClaimState.COMPLETED
        )
        completed = tuple(
            item for item in context if item.state is ClaimState.COMPLETED
        )
        return CoordinationTask(record, active, completed)

    def submit_coordination_decision(
        self, project_id: str, claim_id: str, decision: Decision
    ) -> ClaimRecord:
        self.run_deadline_checks(project_id)
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
        self.store.queue_coordination(
            claim.project_id, claim.claim_id, now=self._now_iso()
        )
        return self.store.get(claim.project_id, claim.claim_id)

    def _reevaluate_waiters(self, trigger: ClaimRecord) -> None:
        active = self.store.list_active(
            trigger.claim.project_id, exclude=trigger.claim.claim_id
        )
        for record in active:
            if record.state is not ClaimState.WAITING or record.decision is None:
                continue
            if trigger.claim.claim_id not in record.decision.wait_for:
                continue
            self.store.transition(
                record.claim.project_id,
                record.claim.claim_id,
                {ClaimState.WAITING},
                ClaimState.ASSESSING,
            )
            others = self.store.list_coordination_context(
                record.claim.project_id, exclude=record.claim.claim_id
            )
            self._assess_or_queue(record.claim, others)
