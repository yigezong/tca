from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from .domain import (
    Claim,
    ClaimRecord,
    ClaimState,
    CoordinationTask,
    DependencyMode,
    Decision,
    Handoff,
    CoordinationAdvice,
    SubgoalEvent,
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
        allow_needs_human: bool = True,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = store
        self.rule_checker = rule_checker
        self.execution_timeout = execution_timeout
        self.heartbeat_timeout = heartbeat_timeout
        self.m_decision_timeout = m_decision_timeout
        self.allow_needs_human = allow_needs_human
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
            # M did not answer, so a cycle caused only by path overlap must not
            # consume both agents' execution windows. C elects this assessing
            # claim as the deterministic first mover. Explicit dependency cycles
            # retain their normal needs-human behavior below.
            if decision.reason == "planned paths overlap active claims":
                decision = Decision(
                    state=ClaimState.APPROVED_WITH_CONSTRAINTS,
                    reason=(
                        "M decision deadline elapsed; C broke a path-overlap "
                        f"wait cycle deterministically (wait_for={', '.join(decision.wait_for)})"
                    ),
                    constraints=(
                        "Implement only your declared planned_paths on your work_branch.",
                        "The waiting overlapping claim will be re-evaluated after completion.",
                    ),
                )
            elif not self.allow_needs_human:
                constraints = tuple(
                    sorted(
                        {
                            item.claim.work_branch
                            for item in active
                            if item.claim.claim_id in decision.wait_for
                        }
                    )
                )
                decision = Decision(
                    state=ClaimState.APPROVED_WITH_CONSTRAINTS,
                    reason=(
                        "M decision deadline elapsed; wait cycle detected but "
                        "needs_human is disabled on this server, so C breaks the "
                        f"cycle deterministically (wait_for={', '.join(decision.wait_for)})"
                    ),
                    constraints=constraints,
                )
            else:
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

    def publish_handoff(
        self, project_id: str, claim_id: str, handoff: Handoff
    ) -> ClaimRecord:
        """Publish an immutable interface checkpoint while still executing.

        Publishing never marks the owner complete: it releases successors to
        work against a stable contract while the owner finishes independent
        implementation.  The recorded commit preserves authorship in the
        final Git history.
        """
        self.run_deadline_checks(project_id)
        record = self.store.get(project_id, claim_id)
        if record.state is not ClaimState.EXECUTING:
            raise ValueError("handoff can only be published by an executing claim")
        if handoff.claim_id != claim_id:
            raise ValueError("handoff claim_id must match the publishing claim")
        owned = set(record.claim.owned_symbols)
        if owned and not set(handoff.exported_symbols).issubset(owned):
            raise ValueError("handoff exported_symbols must be owned by the publisher")
        if owned and not set(handoff.transferable_symbols).issubset(owned):
            raise ValueError("transferable_symbols must be owned by the publisher")
        self.store.publish_handoff(project_id, claim_id, handoff, self._now_iso())
        published = self.store.get(project_id, claim_id)
        self._reevaluate_interface_waiters(published)
        return published

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

    def request_requirements_clarification(
        self,
        project_id: str,
        claim_id: str,
        question: str,
        context: str = "",
    ) -> ClaimRecord:
        """Pause only an ambiguous product decision for its owning P.

        This is deliberately not an escalation route for Git conflicts,
        rebases, or implementation uncertainty: those remain the executing
        A's responsibility.  The persisted payload is natural language only;
        no patch or private worktree content is accepted by TCA.
        """
        self.run_deadline_checks(project_id)
        if not self.allow_needs_human:
            raise ValueError("requirements clarification is disabled on this server")
        question = question.strip()
        if not question:
            raise ValueError("requirements clarification requires a question")
        record = self.store.get(project_id, claim_id)
        if record.state is not ClaimState.EXECUTING:
            raise ValueError(
                "requirements clarification may only pause an executing claim"
            )
        note = f"requirements question: {question}"
        if context.strip():
            note += f"\ncontext: {context.strip()}"
        return self.store.transition(
            project_id,
            claim_id,
            {ClaimState.EXECUTING},
            ClaimState.NEEDS_HUMAN,
            decision=Decision(
                ClaimState.NEEDS_HUMAN,
                note,
                conflicts_with=(),
            ),
        )

    def record_subgoal_event(self, event: SubgoalEvent) -> SubgoalEvent:
        """Persist one coarse start/finish/integration event from the owning A."""
        self.run_deadline_checks(event.project_id)
        record = self.store.get(event.project_id, event.claim_id)
        if record.claim.author != event.author:
            raise ValueError("subgoal event author must own the claim")
        if record.state is not ClaimState.EXECUTING:
            raise ValueError("subgoal events require an executing claim")
        return self.store.record_subgoal_event(event)

    def get_integration_context(self, project_id: str, claim_id: str) -> dict[str, object]:
        """Give the later integrator only relevant public coordination context."""
        record = self.store.get(project_id, claim_id)
        return {
            "claim": record.to_mapping(),
            "related_claims": [item.to_mapping() for item in self.store.list_coordination_context(project_id, exclude=claim_id)],
            "subgoal_events": [item.to_mapping() for item in self.store.list_subgoal_events(project_id)],
            "advice": [item.to_mapping() for item in self.store.list_coordination_advice(project_id, claim_id)],
            "instruction": "Rebase onto relevant newer work and resolve code, API, and Git conflicts yourself. Escalate only product-requirements ambiguity.",
        }

    def publish_coordination_advice(self, advice: CoordinationAdvice) -> CoordinationAdvice:
        """M publishes non-blocking natural-language advice for one owning A."""
        self.run_deadline_checks(advice.project_id)
        self.store.get(advice.project_id, advice.claim_id)
        if not advice.message.strip():
            raise ValueError("coordination advice requires a message")
        return self.store.publish_coordination_advice(advice)

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
        return self._coordination_task_from_key(claim_key)

    def lease_coordination_task(
        self,
        project_id: str | None,
        worker_id: str,
        lease_seconds: float = 300.0,
    ) -> CoordinationTask | None:
        """Lease one task to an M worker so another worker cannot also decide it."""
        self.run_deadline_checks(project_id)
        claim_key = self.store.claim_coordination(
            project_id, worker_id, lease_seconds, now=self._now_iso()
        )
        return self._coordination_task_from_key(claim_key)

    def _coordination_task_from_key(
        self, claim_key: tuple[str, str] | None
    ) -> CoordinationTask | None:
        if claim_key is None:
            return None
        task_project_id, claim_id = claim_key
        record = self.store.get(task_project_id, claim_id)
        if record.state not in {
            ClaimState.ASSESSING,
            ClaimState.APPROVED,
            ClaimState.APPROVED_WITH_CONSTRAINTS,
            ClaimState.EXECUTING,
        }:
            raise ValueError(
                f"claim {task_project_id}:{claim_id} is no longer coordination-active"
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
        self,
        project_id: str,
        claim_id: str,
        decision: Decision,
        worker_id: str | None = None,
    ) -> ClaimRecord:
        self.run_deadline_checks(project_id)
        if decision.state is ClaimState.NEEDS_HUMAN and not self.allow_needs_human:
            record = self.store.get(project_id, claim_id)
            active = self.store.list_active(project_id, exclude=claim_id)
            hard = self.rule_checker.check(record.claim, active)
            if hard is None:
                decision = Decision(
                    state=(
                        ClaimState.APPROVED_WITH_CONSTRAINTS
                        if decision.constraints
                        else ClaimState.APPROVED
                    ),
                    reason=(
                        "needs_human is disabled on this server; C resolved "
                        "the claim deterministically"
                    ),
                    constraints=decision.constraints,
                )
            else:
                decision = hard
        allowed_states = {
            ClaimState.APPROVED,
            ClaimState.APPROVED_WITH_CONSTRAINTS,
            ClaimState.WAITING,
            ClaimState.NEEDS_HUMAN,
            ClaimState.BLOCKED,
        }
        if decision.state not in allowed_states:
            raise ValueError(f"M cannot move a Claim to {decision.state.value}")
        if worker_id is not None:
            self.store.validate_coordination_lease(
                project_id, claim_id, worker_id, now=self._now_iso()
            )
        current = self.store.get(project_id, claim_id)
        if current.state in {
            ClaimState.APPROVED,
            ClaimState.APPROVED_WITH_CONSTRAINTS,
            ClaimState.EXECUTING,
        }:
            if decision.state not in {
                ClaimState.APPROVED,
                ClaimState.APPROVED_WITH_CONSTRAINTS,
            }:
                raise ValueError(
                    "M may not block an already-running asynchronous claim; "
                    "only the owning A may request requirements clarification"
                )
            self.store.complete_coordination(
                project_id, claim_id, worker_id=worker_id
            )
            return current
        record = self.store.transition(
            project_id,
            claim_id,
            {ClaimState.ASSESSING},
            decision.state,
            decision=decision,
        )
        self.store.complete_coordination(project_id, claim_id, worker_id=worker_id)
        return record

    def resolve_privileged_review(
        self, project_id: str, claim_id: str, decision: Decision
    ) -> ClaimRecord:
        """Record a human P's natural-language alignment outcome.

        The server never receives or reads a patch. Only the resulting minimal
        constraints and requirements for the owning A are persisted.
        """
        allowed_states = {
            ClaimState.APPROVED,
            ClaimState.APPROVED_WITH_CONSTRAINTS,
            ClaimState.WAITING,
            ClaimState.BLOCKED,
        }
        if decision.state not in allowed_states:
            raise ValueError(f"P cannot move a Claim to {decision.state.value}")
        if decision.uncertainty is not None and decision.uncertainty >= 0.7:
            decision = Decision(
                state=decision.state,
                reason=(
                    f"{decision.reason} P uncertainty={decision.uncertainty:.2f}; "
                    "no strong preference: any implementation preserving the "
                    "declared behavior contracts is acceptable."
                ),
                constraints=decision.constraints,
                wait_for=decision.wait_for,
                conflicts_with=decision.conflicts_with,
                uncertainty=decision.uncertainty,
                boundary_contract=decision.boundary_contract,
            )
        return self.store.transition(
            project_id,
            claim_id,
            {ClaimState.NEEDS_HUMAN},
            decision.state,
            decision=decision,
        )

    def _assess_or_queue(
        self, claim: Claim, active: list[ClaimRecord]
    ) -> ClaimRecord:
        interface_decision, ignored_claims = self._interface_handoff_decision(
            claim, active
        )
        if interface_decision is not None and interface_decision.state in {
            ClaimState.WAITING,
            ClaimState.NEEDS_HUMAN,
        }:
            return self.store.transition(
                claim.project_id,
                claim.claim_id,
                {ClaimState.ASSESSING},
                interface_decision.state,
                decision=interface_decision,
            )
        scoped_active = [
            record for record in active if record.claim.claim_id not in ignored_claims
        ]
        hard_decision = self.rule_checker.check(claim, scoped_active)
        if hard_decision is not None:
            # A path overlap is a coordination signal, not a reason to make
            # every participant wait for M or for all peers to arrive.  The
            # later integrator owns rebase/merge resolution; C only records
            # conservative scope constraints.  Explicit interface dependencies
            # above remain the one intentional waiting case.
            if hard_decision.state is ClaimState.WAITING:
                constraints = tuple(
                    dict.fromkeys(
                        (
                            *hard_decision.constraints,
                            "Proceed on your declared responsibility boundary; "
                            "before completion, rebase onto newer related work "
                            "and resolve any implementation or Git conflicts yourself.",
                        )
                    )
                )
                return self.store.transition(
                    claim.project_id,
                    claim.claim_id,
                    {ClaimState.ASSESSING},
                    ClaimState.APPROVED_WITH_CONSTRAINTS,
                    decision=Decision(
                        ClaimState.APPROVED_WITH_CONSTRAINTS,
                        "potential shared boundary detected; parallel execution is allowed",
                        constraints=constraints,
                        conflicts_with=hard_decision.conflicts_with,
                    ),
                )
            return self.store.transition(
                claim.project_id,
                claim.claim_id,
                {ClaimState.ASSESSING},
                hard_decision.state,
                decision=hard_decision,
            )
        if interface_decision is not None:
            return self.store.transition(
                claim.project_id,
                claim.claim_id,
                {ClaimState.ASSESSING},
                interface_decision.state,
                decision=interface_decision,
            )
        # Normal independent work does not wait for an M approval.  Queue a
        # read/advise task after approval so M can still observe the changing
        # project state without becoming the critical path.
        record = self.store.transition(
            claim.project_id,
            claim.claim_id,
            {ClaimState.ASSESSING},
            ClaimState.APPROVED,
            decision=Decision(
                ClaimState.APPROVED,
                "no explicit interface dependency or shared boundary requires waiting",
            ),
        )
        self.store.queue_coordination(
            claim.project_id, claim.claim_id, now=self._now_iso()
        )
        return record

    def _interface_handoff_decision(
        self, claim: Claim, context: list[ClaimRecord]
    ) -> tuple[Decision | None, set[str]]:
        """Resolve interface-first dependencies without forcing idle waits."""
        if claim.dependency_mode is not DependencyMode.AFTER_INTERFACE:
            return None, set()
        by_id = {record.claim.claim_id: record for record in context}
        missing: list[str] = []
        handoffs: list[Handoff] = []
        for predecessor_id in claim.handoff_from:
            predecessor = by_id.get(predecessor_id)
            if predecessor is None or predecessor.handoff is None:
                missing.append(predecessor_id)
            else:
                handoffs.append(predecessor.handoff)
        if missing:
            return (
                Decision(
                    state=ClaimState.WAITING,
                    reason="waiting for interface handoff",
                    wait_for=tuple(missing),
                ),
                set(),
            )
        transferable = {
            symbol for handoff in handoffs for symbol in handoff.transferable_symbols
        }
        requested = set(claim.takes_over_symbols)
        if not requested.issubset(transferable):
            return (
                Decision(
                    state=ClaimState.NEEDS_HUMAN,
                    reason=(
                        "ownership transfer requested without predecessor approval: "
                        + ", ".join(sorted(requested - transferable))
                    ),
                    conflicts_with=claim.handoff_from,
                ),
                set(),
            )
        constraints = tuple(
            "Rebase onto {commit} from {claim_id}; preserve its behavior contracts: {contracts}".format(
                commit=handoff.commit,
                claim_id=handoff.claim_id,
                contracts="; ".join(handoff.behavior_contracts),
            )
            for handoff in handoffs
        )
        if requested:
            constraints += (
                "Ownership transfer approved for: " + ", ".join(sorted(requested)),
            )
        return (
            Decision(
                state=ClaimState.APPROVED_WITH_CONSTRAINTS,
                reason="required interface handoff is published; successor may proceed",
                constraints=constraints,
                conflicts_with=claim.handoff_from,
            ),
            set(claim.handoff_from),
        )

    def _reevaluate_interface_waiters(self, trigger: ClaimRecord) -> None:
        for record in self.store.list_active(trigger.claim.project_id):
            if (
                record.state is not ClaimState.WAITING
                or record.decision is None
                or record.decision.reason != "waiting for interface handoff"
                or trigger.claim.claim_id not in record.decision.wait_for
            ):
                continue
            self.store.transition(
                record.claim.project_id,
                record.claim.claim_id,
                {ClaimState.WAITING},
                ClaimState.ASSESSING,
            )
            context = self.store.list_coordination_context(
                record.claim.project_id, exclude=record.claim.claim_id
            )
            self._assess_or_queue(record.claim, context)

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
            # A waiter caused solely by an overlapping path has already received
            # its ordering decision. Once its predecessor completes, it can move
            # forward with an explicit branch-integration constraint; making it
            # wait for M again recreates the case-20 liveness hole.
            if record.decision.reason == "planned paths overlap active claims":
                remaining = self.store.list_active(
                    record.claim.project_id, exclude=record.claim.claim_id
                )
                if self.rule_checker.check(record.claim, remaining) is None:
                    constraints = ()
                    state = ClaimState.APPROVED
                    reason = "overlapping predecessor is integrated; approved by C"
                    if trigger.state is ClaimState.COMPLETED:
                        state = ClaimState.APPROVED_WITH_CONSTRAINTS
                        constraints = (
                            "Inspect and incorporate the completed overlapping work before editing: "
                            + trigger.claim.work_branch,
                        )
                        reason = "overlapping predecessor completed; approved by C with integration constraint"
                    self.store.transition(
                        record.claim.project_id,
                        record.claim.claim_id,
                        {ClaimState.ASSESSING},
                        state,
                        decision=Decision(state, reason, constraints=constraints),
                    )
                    continue
            others = self.store.list_coordination_context(
                record.claim.project_id, exclude=record.claim.claim_id
            )
            self._assess_or_queue(record.claim, others)
