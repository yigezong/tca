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

    def list_coordination_context(
        self, project_id: str, exclude: str | None = None
    ) -> list[ClaimRecord]: ...

    def queue_coordination(
        self, project_id: str, claim_id: str, now: str | None = None
    ) -> None: ...

    def next_coordination_claim(
        self, project_id: str | None = None
    ) -> tuple[str, str] | None: ...

    def claim_coordination(
        self,
        project_id: str | None,
        worker_id: str,
        lease_seconds: float,
        now: str | None = None,
    ) -> tuple[str, str] | None: ...

    def complete_coordination(
        self,
        project_id: str,
        claim_id: str,
        worker_id: str | None = None,
        now: str | None = None,
    ) -> None: ...

    def validate_coordination_lease(
        self, project_id: str, claim_id: str, worker_id: str, now: str | None = None
    ) -> None: ...

    def record_execution_start(
        self, project_id: str, claim_id: str, ts: str
    ) -> None: ...

    def update_progress(
        self, project_id: str, claim_id: str, progress: str, ts: str
    ) -> None: ...

    def coordination_created_at(
        self, project_id: str, claim_id: str
    ) -> str | None: ...

    def list_active_all(self) -> list[ClaimRecord]: ...
