from __future__ import annotations

from typing import Protocol

from .domain import Claim, ClaimRecord, ClaimState, Decision


class Store(Protocol):
    def save_received(self, claim: Claim) -> ClaimRecord: ...

    def transition(
        self,
        project_id: str,
        claim_id: str,
        expected: set[ClaimState],
        target: ClaimState,
        decision: Decision | None = None,
    ) -> ClaimRecord: ...

    def get(self, project_id: str, claim_id: str) -> ClaimRecord: ...

    def list_active(
        self, project_id: str, exclude: str | None = None
    ) -> list[ClaimRecord]: ...

    def queue_coordination(self, project_id: str, claim_id: str) -> None: ...

    def next_coordination_claim(
        self, project_id: str | None = None
    ) -> tuple[str, str] | None: ...

    def complete_coordination(self, project_id: str, claim_id: str) -> None: ...
