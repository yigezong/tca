from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

from .domain import ACTIVE_STATES, Claim, ClaimRecord, ClaimState, Decision


def _now() -> str:
    return datetime.now(UTC).isoformat()


class ClaimStore:
    def __init__(self, database: str | Path = ":memory:") -> None:
        self._connection = sqlite3.connect(str(database), check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._connection:
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS claims (
                    claim_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    claim_version INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    decision TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS claims_project_state
                    ON claims(project_id, state);
                CREATE TABLE IF NOT EXISTS audit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    claim_id TEXT NOT NULL,
                    event TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS coordination_tasks (
                    claim_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    claim_revision INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS coordination_tasks_pending
                    ON coordination_tasks(status, created_at);
                """
            )

    def close(self) -> None:
        self._connection.close()

    def save_received(self, claim: Claim) -> ClaimRecord:
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT claim_version, state, revision FROM claims WHERE claim_id = ?",
                (claim.claim_id,),
            ).fetchone()
            now = _now()
            if row is None:
                revision = 1
                self._connection.execute(
                    """INSERT INTO claims
                    (claim_id, project_id, claim_version, state, revision, payload, decision, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, NULL, ?)""",
                    (
                        claim.claim_id,
                        claim.project_id,
                        claim.version,
                        ClaimState.RECEIVED.value,
                        revision,
                        json.dumps(claim.to_mapping()),
                        now,
                    ),
                )
                event = "claim_received"
            else:
                if claim.version <= row["claim_version"]:
                    raise ValueError(
                        f"claim {claim.claim_id} version must be greater than {row['claim_version']}"
                    )
                if ClaimState(row["state"]) not in ACTIVE_STATES:
                    raise ValueError(f"claim {claim.claim_id} is already terminal")
                revision = row["revision"] + 1
                self._connection.execute(
                    """UPDATE claims SET claim_version = ?, state = ?, revision = ?,
                    payload = ?, decision = NULL, updated_at = ? WHERE claim_id = ?""",
                    (
                        claim.version,
                        ClaimState.RECEIVED.value,
                        revision,
                        json.dumps(claim.to_mapping()),
                        now,
                        claim.claim_id,
                    ),
                )
                event = "claim_amended"
            self._audit(claim.claim_id, event, {"claim_version": claim.version})
        return self.get(claim.claim_id)

    def transition(
        self,
        claim_id: str,
        expected: set[ClaimState],
        target: ClaimState,
        decision: Decision | None = None,
        detail: dict[str, object] | None = None,
    ) -> ClaimRecord:
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT state, revision FROM claims WHERE claim_id = ?", (claim_id,)
            ).fetchone()
            if row is None:
                raise KeyError(claim_id)
            current = ClaimState(row["state"])
            if current not in expected:
                raise ValueError(
                    f"illegal transition for {claim_id}: {current.value} -> {target.value}"
                )
            revision = row["revision"] + 1
            encoded_decision = json.dumps(decision.to_mapping()) if decision else None
            self._connection.execute(
                """UPDATE claims SET state = ?, revision = ?, decision = COALESCE(?, decision),
                updated_at = ? WHERE claim_id = ?""",
                (target.value, revision, encoded_decision, _now(), claim_id),
            )
            self._audit(
                claim_id,
                "state_transition",
                {"from": current.value, "to": target.value, **(detail or {})},
            )
        return self.get(claim_id)

    def get(self, claim_id: str) -> ClaimRecord:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM claims WHERE claim_id = ?", (claim_id,)
            ).fetchone()
        if row is None:
            raise KeyError(claim_id)
        return self._decode(row)

    def list_active(self, project_id: str, exclude: str | None = None) -> list[ClaimRecord]:
        values = tuple(state.value for state in ACTIVE_STATES)
        placeholders = ",".join("?" for _ in values)
        query = f"SELECT * FROM claims WHERE project_id = ? AND state IN ({placeholders})"
        parameters: list[object] = [project_id, *values]
        if exclude is not None:
            query += " AND claim_id != ?"
            parameters.append(exclude)
        query += " ORDER BY updated_at"
        with self._lock:
            rows = self._connection.execute(query, parameters).fetchall()
        return [self._decode(row) for row in rows]

    def record_activity(
        self,
        claim_id: str,
        event: str,
        detail: dict[str, object] | None = None,
    ) -> ClaimRecord:
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT state, revision FROM claims WHERE claim_id = ?", (claim_id,)
            ).fetchone()
            if row is None:
                raise KeyError(claim_id)
            state = ClaimState(row["state"])
            if state not in ACTIVE_STATES:
                raise ValueError(f"claim {claim_id} is not active")
            self._connection.execute(
                "UPDATE claims SET revision = ?, updated_at = ? WHERE claim_id = ?",
                (row["revision"] + 1, _now(), claim_id),
            )
            self._audit(claim_id, event, detail or {})
        return self.get(claim_id)

    def queue_coordination(self, claim_id: str) -> None:
        record = self.get(claim_id)
        now = _now()
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO coordination_tasks
                (claim_id, project_id, claim_revision, status, created_at, updated_at)
                VALUES (?, ?, ?, 'pending', ?, ?)
                ON CONFLICT(claim_id) DO UPDATE SET
                    project_id = excluded.project_id,
                    claim_revision = excluded.claim_revision,
                    status = 'pending',
                    created_at = excluded.created_at,
                    updated_at = excluded.updated_at""",
                (
                    claim_id,
                    record.claim.project_id,
                    record.revision,
                    now,
                    now,
                ),
            )
            self._audit(
                claim_id,
                "coordination_requested",
                {"claim_revision": record.revision},
            )

    def next_coordination_claim_id(self, project_id: str | None = None) -> str | None:
        query = "SELECT claim_id FROM coordination_tasks WHERE status = 'pending'"
        parameters: list[object] = []
        if project_id:
            query += " AND project_id = ?"
            parameters.append(project_id)
        query += " ORDER BY created_at LIMIT 1"
        with self._lock:
            row = self._connection.execute(query, parameters).fetchone()
        return str(row["claim_id"]) if row else None

    def complete_coordination(self, claim_id: str, coordinator_id: str) -> None:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """UPDATE coordination_tasks SET status = 'completed', updated_at = ?
                WHERE claim_id = ? AND status = 'pending'""",
                (_now(), claim_id),
            )
            if cursor.rowcount != 1:
                raise ValueError(f"claim {claim_id} has no pending coordination task")
            self._audit(
                claim_id,
                "coordination_completed",
                {"coordinator_id": coordinator_id},
            )

    def audit_events(self, claim_id: str) -> list[dict[str, object]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT event, detail, created_at FROM audit_events WHERE claim_id = ? ORDER BY id",
                (claim_id,),
            ).fetchall()
        return [
            {"event": row["event"], "detail": json.loads(row["detail"]), "created_at": row["created_at"]}
            for row in rows
        ]

    def _audit(self, claim_id: str, event: str, detail: dict[str, object]) -> None:
        self._connection.execute(
            "INSERT INTO audit_events(claim_id, event, detail, created_at) VALUES (?, ?, ?, ?)",
            (claim_id, event, json.dumps(detail), _now()),
        )

    @staticmethod
    def _decode(row: sqlite3.Row) -> ClaimRecord:
        decision = json.loads(row["decision"]) if row["decision"] else None
        return ClaimRecord(
            claim=Claim.from_mapping(json.loads(row["payload"])),
            state=ClaimState(row["state"]),
            revision=row["revision"],
            decision=Decision.from_mapping(decision) if decision else None,
            updated_at=row["updated_at"],
        )


def build_store():
    """Use Postgres when DATABASE_URL is set, otherwise local SQLite."""
    import os

    database_url = os.getenv("DATABASE_URL", "").strip()
    if database_url:
        from .postgres_store import PostgresClaimStore

        return PostgresClaimStore(database_url)
    return ClaimStore(Path(os.getenv("TCA_DATABASE", "tca.db")))
