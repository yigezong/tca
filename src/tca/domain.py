from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class ClaimState(StrEnum):
    RECEIVED = "received"
    ASSESSING = "assessing"
    APPROVED = "approved"
    APPROVED_WITH_CONSTRAINTS = "approved_with_constraints"
    WAITING = "waiting"
    NEEDS_HUMAN = "needs_human"
    BLOCKED = "blocked"
    EXECUTING = "executing"
    COMPLETED = "completed"
    INTEGRATED = "integrated"


class DependencyMode(StrEnum):
    """How a claim consumes work owned by another claim."""

    PARALLEL = "parallel"
    AFTER_INTERFACE = "after_interface"
    AFTER_COMPLETION = "after_completion"


class SubgoalEventType(StrEnum):
    STARTED = "subgoal_started"
    FINISHED = "subgoal_finished"
    INTEGRATION_REQUIRED = "integration_required"
    INTEGRATION_FINISHED = "integration_finished"


ACTIVE_STATES = {
    ClaimState.RECEIVED,
    ClaimState.ASSESSING,
    ClaimState.APPROVED,
    ClaimState.APPROVED_WITH_CONSTRAINTS,
    ClaimState.WAITING,
    ClaimState.NEEDS_HUMAN,
    ClaimState.BLOCKED,
    ClaimState.EXECUTING,
}
COORDINATION_CONTEXT_STATES = ACTIVE_STATES | {ClaimState.COMPLETED}


@dataclass(frozen=True, slots=True)
class Claim:
    claim_id: str
    project_id: str
    author: str
    base_commit: str
    work_branch: str
    goal: str
    planned_paths: tuple[str, ...]
    excluded_paths: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    owned_symbols: tuple[str, ...] = ()
    behavior_contracts: tuple[str, ...] = ()
    temporary_checks: tuple[str, ...] = ()
    dependency_mode: DependencyMode = DependencyMode.PARALLEL
    handoff_from: tuple[str, ...] = ()
    takes_over_symbols: tuple[str, ...] = ()
    coordination_group: str = ""
    expected_group_size: int = 1
    version: int = 1

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "Claim":
        required = (
            "claim_id",
            "project_id",
            "author",
            "base_commit",
            "work_branch",
            "goal",
            "planned_paths",
        )
        missing = [key for key in required if not value.get(key)]
        if missing:
            raise ValueError(f"missing required claim fields: {', '.join(missing)}")
        version = int(value.get("version", 1))
        if version < 1:
            raise ValueError("claim version must be positive")
        dependency_mode = DependencyMode(value.get("dependency_mode", "parallel"))
        handoff_from = tuple(str(item) for item in value.get("handoff_from", ()))
        if dependency_mode is DependencyMode.AFTER_INTERFACE and not handoff_from:
            raise ValueError("after_interface claims must declare handoff_from")
        if dependency_mode is DependencyMode.PARALLEL and handoff_from:
            raise ValueError("parallel claims cannot declare handoff_from")
        coordination_group = str(value.get("coordination_group", ""))
        expected_group_size = int(value.get("expected_group_size", 1))
        if expected_group_size < 1:
            raise ValueError("expected_group_size must be positive")
        if expected_group_size > 1 and not coordination_group:
            raise ValueError("multi-claim coordination requires coordination_group")
        return cls(
            claim_id=str(value["claim_id"]),
            project_id=str(value["project_id"]),
            author=str(value["author"]),
            base_commit=str(value["base_commit"]),
            work_branch=str(value["work_branch"]),
            goal=str(value["goal"]),
            planned_paths=tuple(str(path) for path in value["planned_paths"]),
            excluded_paths=tuple(str(path) for path in value.get("excluded_paths", ())),
            dependencies=tuple(str(item) for item in value.get("dependencies", ())),
            owned_symbols=tuple(str(item) for item in value.get("owned_symbols", ())),
            behavior_contracts=tuple(
                str(item) for item in value.get("behavior_contracts", ())
            ),
            temporary_checks=tuple(str(item) for item in value.get("temporary_checks", ())),
            dependency_mode=dependency_mode,
            handoff_from=handoff_from,
            takes_over_symbols=tuple(
                str(item) for item in value.get("takes_over_symbols", ())
            ),
            coordination_group=coordination_group,
            expected_group_size=expected_group_size,
            version=version,
        )

    def to_mapping(self) -> dict[str, Any]:
        result = asdict(self)
        for key in (
            "planned_paths", "excluded_paths", "dependencies", "owned_symbols",
            "behavior_contracts", "temporary_checks", "handoff_from",
            "takes_over_symbols",
        ):
            result[key] = list(result[key])
        result["dependency_mode"] = self.dependency_mode.value
        return result


@dataclass(frozen=True, slots=True)
class Handoff:
    """A stable interface checkpoint published by the current owner.

    It deliberately contains contracts and commit provenance, never source code.
    A successor can rebase onto ``commit`` and continue working without waiting
    for the owner to finish unrelated implementation details.
    """

    claim_id: str
    commit: str
    exported_symbols: tuple[str, ...]
    behavior_contracts: tuple[str, ...]
    notes: str = ""
    transferable_symbols: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "Handoff":
        required = ("claim_id", "commit", "exported_symbols", "behavior_contracts")
        missing = [key for key in required if not value.get(key)]
        if missing:
            raise ValueError(f"missing required handoff fields: {', '.join(missing)}")
        return cls(
            claim_id=str(value["claim_id"]),
            commit=str(value["commit"]),
            exported_symbols=tuple(str(item) for item in value["exported_symbols"]),
            behavior_contracts=tuple(
                str(item) for item in value["behavior_contracts"]
            ),
            notes=str(value.get("notes", "")),
            transferable_symbols=tuple(
                str(item) for item in value.get("transferable_symbols", ())
            ),
        )

    def to_mapping(self) -> dict[str, Any]:
        result = asdict(self)
        for key in (
            "exported_symbols",
            "behavior_contracts",
            "transferable_symbols",
        ):
            result[key] = list(result[key])
        return result


@dataclass(frozen=True, slots=True)
class Decision:
    state: ClaimState
    reason: str
    constraints: tuple[str, ...] = ()
    wait_for: tuple[str, ...] = ()
    conflicts_with: tuple[str, ...] = ()
    uncertainty: float | None = None
    boundary_contract: str = ""

    def to_mapping(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "reason": self.reason,
            "constraints": list(self.constraints),
            "wait_for": list(self.wait_for),
            "conflicts_with": list(self.conflicts_with),
            "uncertainty": self.uncertainty,
            "boundary_contract": self.boundary_contract,
        }

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "Decision":
        uncertainty = (
            float(value["uncertainty"])
            if value.get("uncertainty") is not None
            else None
        )
        if uncertainty is not None and not 0.0 <= uncertainty <= 1.0:
            raise ValueError("uncertainty must be between 0 and 1")
        return cls(
            state=ClaimState(value["state"]),
            reason=str(value["reason"]),
            constraints=tuple(value.get("constraints", ())),
            wait_for=tuple(value.get("wait_for", ())),
            conflicts_with=tuple(value.get("conflicts_with", ())),
            uncertainty=uncertainty,
            boundary_contract=str(value.get("boundary_contract", "")),
        )


@dataclass(frozen=True, slots=True)
class ClaimRecord:
    claim: Claim
    state: ClaimState
    revision: int
    decision: Decision | None = None
    updated_at: str = field(default="")
    executing_since: str = field(default="")
    last_heartbeat: str = field(default="")
    progress: str = field(default="")
    handoff: Handoff | None = None

    def to_mapping(self) -> dict[str, Any]:
        return {
            "claim": self.claim.to_mapping(),
            "state": self.state.value,
            "revision": self.revision,
            "decision": self.decision.to_mapping() if self.decision else None,
            "updated_at": self.updated_at,
            "executing_since": self.executing_since,
            "last_heartbeat": self.last_heartbeat,
            "progress": self.progress,
            "handoff": self.handoff.to_mapping() if self.handoff else None,
        }


@dataclass(frozen=True, slots=True)
class CoordinationTask:
    claim: ClaimRecord
    active_claims: tuple[ClaimRecord, ...]
    completed_claims: tuple[ClaimRecord, ...]

    def to_mapping(self) -> dict[str, Any]:
        return {
            "claim": self.claim.to_mapping(),
            "active_claims": [record.to_mapping() for record in self.active_claims],
            "completed_claims": [
                record.to_mapping() for record in self.completed_claims
            ],
        }


@dataclass(frozen=True, slots=True)
class SubgoalEvent:
    """A coarse collaboration event, never a stream of code/tool actions."""

    project_id: str
    claim_id: str
    subgoal_id: str
    author: str
    event_type: SubgoalEventType
    goal: str
    planned_paths: tuple[str, ...] = ()
    note: str = ""
    created_at: str = ""

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "SubgoalEvent":
        required = ("project_id", "claim_id", "subgoal_id", "author", "event_type", "goal")
        missing = [key for key in required if not value.get(key)]
        if missing:
            raise ValueError(f"missing subgoal event fields: {', '.join(missing)}")
        return cls(
            project_id=str(value["project_id"]), claim_id=str(value["claim_id"]),
            subgoal_id=str(value["subgoal_id"]), author=str(value["author"]),
            event_type=SubgoalEventType(value["event_type"]), goal=str(value["goal"]),
            planned_paths=tuple(str(path) for path in value.get("planned_paths", ())),
            note=str(value.get("note", "")), created_at=str(value.get("created_at", "")),
        )

    def to_mapping(self) -> dict[str, Any]:
        result = asdict(self)
        result["event_type"] = self.event_type.value
        result["planned_paths"] = list(self.planned_paths)
        return result


@dataclass(frozen=True, slots=True)
class CoordinationAdvice:
    project_id: str
    claim_id: str
    advisor: str
    message: str
    related_subgoals: tuple[str, ...] = ()
    created_at: str = ""

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "CoordinationAdvice":
        required = ("project_id", "claim_id", "advisor", "message")
        missing = [key for key in required if not value.get(key)]
        if missing:
            raise ValueError(f"missing coordination advice fields: {', '.join(missing)}")
        return cls(
            project_id=str(value["project_id"]), claim_id=str(value["claim_id"]),
            advisor=str(value["advisor"]), message=str(value["message"]),
            related_subgoals=tuple(str(item) for item in value.get("related_subgoals", ())),
            created_at=str(value.get("created_at", "")),
        )

    def to_mapping(self) -> dict[str, Any]:
        result = asdict(self)
        result["related_subgoals"] = list(self.related_subgoals)
        return result


def allowed_actions(state: ClaimState) -> tuple[str, ...]:
    return {
        ClaimState.RECEIVED: (),
        ClaimState.ASSESSING: ("get_claim_state",),
        ClaimState.APPROVED: ("start_execution",),
        ClaimState.APPROVED_WITH_CONSTRAINTS: ("start_execution",),
        ClaimState.WAITING: ("get_claim_state",),
        ClaimState.NEEDS_HUMAN: ("get_claim_state",),
        ClaimState.BLOCKED: ("get_claim_state",),
        ClaimState.EXECUTING: ("report_progress", "publish_handoff", "complete_claim", "request_requirements_clarification", "record_subgoal_event", "get_integration_context"),
        ClaimState.COMPLETED: (),
        ClaimState.INTEGRATED: (),
    }[state]
