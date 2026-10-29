from __future__ import annotations

from typing import Protocol

from .domain import Claim, ClaimRecord, ClaimState, CoordinationAdvice, Decision, Handoff, SubgoalEvent


class Store(Protocol):
    def create_project(self, project_id: str, owner_login: str) -> None: ...

    def project_owner(self, project_id: str) -> str | None: ...

    def project_manager(self, project_id: str) -> str | None: ...

    def set_project_manager(self, project_id: str, login: str) -> None: ...

    def create_auth_challenge(self, login: str) -> dict: ...

    def consume_auth_challenge(self, challenge_id: str, login: str) -> str | None: ...

    def create_auth_session(self, token_hash: str, login: str) -> str: ...

    def auth_session_login(self, token_hash: str) -> str | None: ...

    def list_claim_revisions(self, project_id: str, claim_id: str) -> list[dict]: ...

    def list_claim_events(self, project_id: str, claim_id: str) -> list[dict]: ...

    def list_projects(self) -> list[tuple[str, str]]: ...

    def reserve_agent_claim(self, project_id: str, author: str, claim_id: str) -> bool: ...

    def release_agent_claim(self, project_id: str, author: str, claim_id: str) -> None: ...

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

    def publish_handoff(
        self, project_id: str, claim_id: str, handoff: Handoff, ts: str
    ) -> None: ...

    def coordination_created_at(
        self, project_id: str, claim_id: str
    ) -> str | None: ...

    def list_active_all(self) -> list[ClaimRecord]: ...

    def record_subgoal_event(self, event: SubgoalEvent) -> SubgoalEvent: ...

    def list_subgoal_events(self, project_id: str, claim_id: str | None = None) -> list[SubgoalEvent]: ...

    def publish_coordination_advice(self, advice: CoordinationAdvice) -> CoordinationAdvice: ...

    def list_coordination_advice(self, project_id: str, claim_id: str) -> list[CoordinationAdvice]: ...
