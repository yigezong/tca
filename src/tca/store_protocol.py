from __future__ import annotations

from typing import Protocol

from .domain import Claim, ClaimRecord, ClaimState, Decision


class Store(Protocol):
    def save_received(self, claim: Claim) -> ClaimRecord: ...

    def transition(
        self,
        claim_id: str,
        expected: set[ClaimState],
        target: ClaimState,
        decision: Decision | None = None,
        detail: dict[str, object] | None = None,
    ) -> ClaimRecord: ...

    def get(self, claim_id: str) -> ClaimRecord: ...

    def list_active(
        self, project_id: str, exclude: str | None = None
    ) -> list[ClaimRecord]: ...

    def record_activity(
        self,
        claim_id: str,
        event: str,
        detail: dict[str, object] | None = None,
    ) -> ClaimRecord: ...

    def queue_coordination(self, claim_id: str) -> None: ...

    def next_coordination_claim_id(
        self, project_id: str | None = None
    ) -> str | None: ...

    def complete_coordination(self, claim_id: str, coordinator_id: str) -> None: ...

    def audit_events(self, claim_id: str) -> list[dict[str, object]]: ...
