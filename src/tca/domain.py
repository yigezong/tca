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
            version=version,
        )

    def to_mapping(self) -> dict[str, Any]:
        result = asdict(self)
        for key in ("planned_paths", "excluded_paths", "dependencies"):
            result[key] = list(result[key])
        return result


@dataclass(frozen=True, slots=True)
class Decision:
    state: ClaimState
    reason: str
    constraints: tuple[str, ...] = ()
    wait_for: tuple[str, ...] = ()
    conflicts_with: tuple[str, ...] = ()

    def to_mapping(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "reason": self.reason,
            "constraints": list(self.constraints),
            "wait_for": list(self.wait_for),
            "conflicts_with": list(self.conflicts_with),
        }

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "Decision":
        return cls(
            state=ClaimState(value["state"]),
            reason=str(value["reason"]),
            constraints=tuple(value.get("constraints", ())),
            wait_for=tuple(value.get("wait_for", ())),
            conflicts_with=tuple(value.get("conflicts_with", ())),
        )


@dataclass(frozen=True, slots=True)
class ClaimRecord:
    claim: Claim
    state: ClaimState
    revision: int
    decision: Decision | None = None
    updated_at: str = field(default="")

    def to_mapping(self) -> dict[str, Any]:
        return {
            "claim": self.claim.to_mapping(),
            "state": self.state.value,
            "revision": self.revision,
            "decision": self.decision.to_mapping() if self.decision else None,
            "updated_at": self.updated_at,
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


def allowed_actions(state: ClaimState) -> tuple[str, ...]:
    return {
        ClaimState.RECEIVED: (),
        ClaimState.ASSESSING: ("get_claim_state",),
        ClaimState.APPROVED: ("start_execution",),
        ClaimState.APPROVED_WITH_CONSTRAINTS: ("start_execution",),
        ClaimState.WAITING: ("get_claim_state",),
        ClaimState.NEEDS_HUMAN: ("get_claim_state",),
        ClaimState.BLOCKED: ("get_claim_state",),
        ClaimState.EXECUTING: ("complete_claim",),
        ClaimState.COMPLETED: (),
        ClaimState.INTEGRATED: (),
    }[state]
