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
            self._ensure_schema()

    def _ensure_schema(self) -> None:
        self._ensure_claims_table()
        self._ensure_coordination_tasks_table()
        self._connection.execute("DROP INDEX IF EXISTS claims_project_state")
        self._connection.execute(
            "CREATE INDEX claims_project_state ON claims(project_id, state)"
        )
        self._connection.execute("DROP INDEX IF EXISTS coordination_tasks_pending")
        self._connection.execute(
            """CREATE INDEX coordination_tasks_pending
            ON coordination_tasks(status, created_at)"""
        )

    def _ensure_claims_table(self) -> None:
        if not self._table_exists("claims"):
            self._create_claims_table()
            return
        if self._primary_key_columns("claims") == ("project_id", "claim_id"):
            return
        self._connection.execute("ALTER TABLE claims RENAME TO claims_old")
        self._create_claims_table()
        self._connection.execute(
            """INSERT INTO claims
            (project_id, claim_id, claim_version, state, revision, payload, decision,
             updated_at)
            SELECT project_id, claim_id, claim_version, state, revision, payload,
                   decision, updated_at
            FROM claims_old"""
        )
        self._connection.execute("DROP TABLE claims_old")

    def _ensure_coordination_tasks_table(self) -> None:
        if not self._table_exists("coordination_tasks"):
            self._create_coordination_tasks_table()
            return
        if self._primary_key_columns("coordination_tasks") == (
            "project_id",
            "claim_id",
        ):
            return
        self._connection.execute(
            "ALTER TABLE coordination_tasks RENAME TO coordination_tasks_old"
        )
        self._create_coordination_tasks_table()
        self._connection.execute(
            """INSERT INTO coordination_tasks
            (project_id, claim_id, claim_revision, status, created_at, updated_at)
            SELECT project_id, claim_id, claim_revision, status, created_at, updated_at
            FROM coordination_tasks_old"""
        )
        self._connection.execute("DROP TABLE coordination_tasks_old")

    def _table_exists(self, name: str) -> bool:
        row = self._connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (name,),
        ).fetchone()
        return row is not None

    def _primary_key_columns(self, table: str) -> tuple[str, ...]:
        rows = self._connection.execute(f"PRAGMA table_info({table})").fetchall()
        keyed = sorted(
            ((int(row["pk"]), str(row["name"])) for row in rows if row["pk"]),
            key=lambda item: item[0],
        )
        return tuple(name for _, name in keyed)

    def _create_claims_table(self) -> None:
        self._connection.execute(
            """CREATE TABLE claims (
                project_id TEXT NOT NULL,
                claim_id TEXT NOT NULL,
                claim_version INTEGER NOT NULL,
                state TEXT NOT NULL,
                revision INTEGER NOT NULL,
                payload TEXT NOT NULL,
                decision TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (project_id, claim_id)
            )"""
        )

    def _create_coordination_tasks_table(self) -> None:
        self._connection.execute(
            """CREATE TABLE coordination_tasks (
                project_id TEXT NOT NULL,
                claim_id TEXT NOT NULL,
                claim_revision INTEGER NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (project_id, claim_id)
            )"""
        )

    def close(self) -> None:
        self._connection.close()

    def save_received(self, claim: Claim) -> ClaimRecord:
        with self._lock, self._connection:
            row = self._connection.execute(
                """SELECT claim_version, state, revision FROM claims
                WHERE project_id = ? AND claim_id = ?""",
                (claim.project_id, claim.claim_id),
            ).fetchone()
            now = _now()
            if row is None:
                revision = 1
                self._connection.execute(
                    """INSERT INTO claims
                    (project_id, claim_id, claim_version, state, revision, payload,
                     decision, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, NULL, ?)""",
                    (
                        claim.project_id,
                        claim.claim_id,
                        claim.version,
                        ClaimState.RECEIVED.value,
                        revision,
                        json.dumps(claim.to_mapping()),
                        now,
                    ),
                )
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
                    payload = ?, decision = NULL, updated_at = ?
                    WHERE project_id = ? AND claim_id = ?""",
                    (
                        claim.version,
                        ClaimState.RECEIVED.value,
                        revision,
                        json.dumps(claim.to_mapping()),
                        now,
                        claim.project_id,
                        claim.claim_id,
                    ),
                )
        return self.get(claim.project_id, claim.claim_id)

    def transition(
        self,
        project_id: str,
        claim_id: str,
        expected: set[ClaimState],
        target: ClaimState,
        decision: Decision | None = None,
    ) -> ClaimRecord:
        with self._lock, self._connection:
            row = self._connection.execute(
                """SELECT state, revision FROM claims
                WHERE project_id = ? AND claim_id = ?""",
                (project_id, claim_id),
            ).fetchone()
            if row is None:
                raise KeyError(f"{project_id}:{claim_id}")
            current = ClaimState(row["state"])
            if current not in expected:
                raise ValueError(
                    f"illegal transition for {claim_id}: {current.value} -> {target.value}"
                )
            revision = row["revision"] + 1
            encoded_decision = json.dumps(decision.to_mapping()) if decision else None
            self._connection.execute(
                """UPDATE claims SET state = ?, revision = ?,
                decision = COALESCE(?, decision), updated_at = ?
                WHERE project_id = ? AND claim_id = ?""",
                (
                    target.value,
                    revision,
                    encoded_decision,
                    _now(),
                    project_id,
                    claim_id,
                ),
            )
        return self.get(project_id, claim_id)

    def get(self, project_id: str, claim_id: str) -> ClaimRecord:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM claims WHERE project_id = ? AND claim_id = ?",
                (project_id, claim_id),
            ).fetchone()
        if row is None:
            raise KeyError(f"{project_id}:{claim_id}")
        return self._decode(row)

    def list_active(
        self, project_id: str, exclude: str | None = None
    ) -> list[ClaimRecord]:
        values = tuple(state.value for state in ACTIVE_STATES)
        placeholders = ",".join("?" for _ in values)
        query = (
            f"SELECT * FROM claims WHERE project_id = ? "
            f"AND state IN ({placeholders})"
        )
        parameters: list[object] = [project_id, *values]
        if exclude is not None:
            query += " AND claim_id != ?"
            parameters.append(exclude)
        query += " ORDER BY updated_at"
        with self._lock:
            rows = self._connection.execute(query, parameters).fetchall()
        return [self._decode(row) for row in rows]

    def queue_coordination(self, project_id: str, claim_id: str) -> None:
        record = self.get(project_id, claim_id)
        now = _now()
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO coordination_tasks
                (project_id, claim_id, claim_revision, status, created_at, updated_at)
                VALUES (?, ?, ?, 'pending', ?, ?)
                ON CONFLICT(project_id, claim_id) DO UPDATE SET
                    claim_revision = excluded.claim_revision,
                    status = 'pending',
                    created_at = excluded.created_at,
                    updated_at = excluded.updated_at""",
                (
                    record.claim.project_id,
                    claim_id,
                    record.revision,
                    now,
                    now,
                ),
            )

    def next_coordination_claim(
        self, project_id: str | None = None
    ) -> tuple[str, str] | None:
        query = (
            "SELECT project_id, claim_id FROM coordination_tasks WHERE status = 'pending'"
        )
        parameters: list[object] = []
        if project_id:
            query += " AND project_id = ?"
            parameters.append(project_id)
        query += " ORDER BY created_at LIMIT 1"
        with self._lock:
            row = self._connection.execute(query, parameters).fetchone()
        return (str(row["project_id"]), str(row["claim_id"])) if row else None

    def complete_coordination(self, project_id: str, claim_id: str) -> None:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """UPDATE coordination_tasks SET status = 'completed', updated_at = ?
                WHERE project_id = ? AND claim_id = ? AND status = 'pending'""",
                (_now(), project_id, claim_id),
            )
            if cursor.rowcount != 1:
                raise ValueError(
                    f"claim {project_id}:{claim_id} has no pending coordination task"
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
    return ClaimStore(Path("tca.db"))
