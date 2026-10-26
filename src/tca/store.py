from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .domain import (
    ACTIVE_STATES,
    COORDINATION_CONTEXT_STATES,
    Claim,
    ClaimRecord,
    ClaimState,
    Decision,
    Handoff,
    CoordinationAdvice,
    SubgoalEvent,
    SubgoalEventType,
)


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
        self._connection.execute("""CREATE TABLE IF NOT EXISTS projects (
            project_id TEXT PRIMARY KEY, owner_login TEXT NOT NULL,
            created_at TEXT NOT NULL)""")
        self._connection.execute("""CREATE TABLE IF NOT EXISTS project_agent_slots (
            project_id TEXT NOT NULL, author TEXT NOT NULL, claim_id TEXT NOT NULL,
            PRIMARY KEY (project_id, author))""")
        self._ensure_claims_table()
        self._ensure_coordination_tasks_table()
        self._connection.execute("""CREATE TABLE IF NOT EXISTS subgoal_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL,
            claim_id TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL)""")
        self._connection.execute("""CREATE TABLE IF NOT EXISTS coordination_advice (
            id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL,
            claim_id TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL)""")
        self._connection.execute("CREATE INDEX IF NOT EXISTS subgoal_events_project_claim ON subgoal_events(project_id, claim_id, id)")
        self._connection.execute("CREATE INDEX IF NOT EXISTS coordination_advice_project_claim ON coordination_advice(project_id, claim_id, id)")
        self._connection.execute("DROP INDEX IF EXISTS claims_project_state")
        self._connection.execute(
            "CREATE INDEX claims_project_state ON claims(project_id, state)"
        )
        self._connection.execute("DROP INDEX IF EXISTS coordination_tasks_pending")
        self._connection.execute(
            """CREATE INDEX coordination_tasks_pending
            ON coordination_tasks(status, lease_expires_at, created_at)"""
        )

    def _ensure_claims_table(self) -> None:
        if not self._table_exists("claims"):
            self._create_claims_table()
            return
        if self._primary_key_columns("claims") == ("project_id", "claim_id"):
            self._ensure_claim_columns()
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

    def _ensure_claim_columns(self) -> None:
        existing = {
            str(row["name"])
            for row in self._connection.execute("PRAGMA table_info(claims)")
        }
        for name in ("executing_since", "last_heartbeat", "progress", "handoff"):
            if name not in existing:
                self._connection.execute(
                    f"ALTER TABLE claims ADD COLUMN {name} TEXT"
                )

    def _ensure_coordination_tasks_table(self) -> None:
        if not self._table_exists("coordination_tasks"):
            self._create_coordination_tasks_table()
            return
        if self._primary_key_columns("coordination_tasks") == (
            "project_id",
            "claim_id",
        ):
            self._ensure_coordination_task_columns()
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
        self._ensure_coordination_task_columns()

    def _ensure_coordination_task_columns(self) -> None:
        existing = {
            str(row["name"])
            for row in self._connection.execute("PRAGMA table_info(coordination_tasks)")
        }
        for name in ("leased_by", "lease_expires_at"):
            if name not in existing:
                self._connection.execute(
                    f"ALTER TABLE coordination_tasks ADD COLUMN {name} TEXT"
                )

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
                executing_since TEXT,
                last_heartbeat TEXT,
                progress TEXT,
                handoff TEXT,
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
                leased_by TEXT,
                lease_expires_at TEXT,
                PRIMARY KEY (project_id, claim_id)
            )"""
        )

    def close(self) -> None:
        self._connection.close()

    def create_project(self, project_id: str, owner_login: str) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO projects(project_id, owner_login, created_at)
                VALUES (?, ?, ?) ON CONFLICT(project_id) DO NOTHING""",
                (project_id, owner_login, _now()),
            )
            if self.project_owner(project_id).lower() != owner_login.lower():
                raise PermissionError("project is already owned by another GitHub user")

    def project_owner(self, project_id: str) -> str | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT owner_login FROM projects WHERE project_id = ?", (project_id,)
            ).fetchone()
            return str(row["owner_login"]) if row else None

    def list_projects(self) -> list[tuple[str, str]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT project_id, owner_login FROM projects ORDER BY project_id"
            ).fetchall()
            return [(str(row["project_id"]), str(row["owner_login"])) for row in rows]

    def reserve_agent_claim(self, project_id: str, author: str, claim_id: str) -> bool:
        """Atomically reserve this developer's single active Claim slot."""
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT claim_id FROM project_agent_slots WHERE project_id = ? AND author = ?",
                (project_id, author),
            ).fetchone()
            if row is not None:
                previous = str(row["claim_id"])
                if previous == claim_id:
                    return False
                state = self._connection.execute(
                    "SELECT state FROM claims WHERE project_id = ? AND claim_id = ?",
                    (project_id, previous),
                ).fetchone()
                if state is None or ClaimState(str(state["state"])) in ACTIVE_STATES:
                    raise PermissionError("one developer may have only one active A Claim per project")
                self._connection.execute(
                    "UPDATE project_agent_slots SET claim_id = ? WHERE project_id = ? AND author = ?",
                    (claim_id, project_id, author),
                )
            else:
                self._connection.execute(
                    "INSERT INTO project_agent_slots VALUES (?, ?, ?)",
                    (project_id, author, claim_id),
                )
            return True

    def release_agent_claim(self, project_id: str, author: str, claim_id: str) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                "DELETE FROM project_agent_slots WHERE project_id = ? AND author = ? AND claim_id = ?",
                (project_id, author, claim_id),
            )

    def save_received(self, claim: Claim) -> ClaimRecord:
        with self._lock, self._connection:
            row = self._connection.execute(
                """SELECT claim_version, state, revision FROM claims
                WHERE project_id = ? AND claim_id = ?""",
                (claim.project_id, claim.claim_id),
            ).fetchone()
            now = _now()
            if row is None:
                if claim.version != 1:
                    raise ValueError(
                        f"new claim {claim.claim_id} must start at version 1"
                    )
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
                    raise ValueError(f"claim {claim.claim_id} is no longer revisable")
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
        return self._list_by_states(project_id, ACTIVE_STATES, exclude)

    def list_coordination_context(
        self, project_id: str, exclude: str | None = None
    ) -> list[ClaimRecord]:
        return self._list_by_states(
            project_id, COORDINATION_CONTEXT_STATES, exclude
        )

    def _list_by_states(
        self,
        project_id: str,
        states: set[ClaimState],
        exclude: str | None,
    ) -> list[ClaimRecord]:
        values = tuple(state.value for state in states)
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

    def queue_coordination(
        self, project_id: str, claim_id: str, now: str | None = None
    ) -> None:
        record = self.get(project_id, claim_id)
        now = now or _now()
        with self._lock, self._connection:
            self._connection.execute(
                """INSERT INTO coordination_tasks
                (project_id, claim_id, claim_revision, status, created_at, updated_at,
                 leased_by, lease_expires_at)
                VALUES (?, ?, ?, 'pending', ?, ?, NULL, NULL)
                ON CONFLICT(project_id, claim_id) DO UPDATE SET
                    claim_revision = excluded.claim_revision,
                    status = 'pending',
                    created_at = excluded.created_at,
                    updated_at = excluded.updated_at,
                    leased_by = NULL,
                    lease_expires_at = NULL""",
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

    def claim_coordination(
        self,
        project_id: str | None,
        worker_id: str,
        lease_seconds: float,
        now: str | None = None,
    ) -> tuple[str, str] | None:
        """Atomically lease the oldest available coordination task.

        A lease expires rather than being lost when a coordinator crashes.  The
        caller must use the same worker_id when submitting its decision.
        """
        if not worker_id.strip():
            raise ValueError("worker_id is required to lease a coordination task")
        now = now or _now()
        expires_at = (
            datetime.fromisoformat(now) + timedelta(seconds=max(1.0, lease_seconds))
        ).isoformat()
        query = """SELECT project_id, claim_id FROM coordination_tasks
            WHERE (status = 'pending' OR (status = 'leased' AND lease_expires_at <= ?))"""
        parameters: list[object] = [now]
        if project_id:
            query += " AND project_id = ?"
            parameters.append(project_id)
        query += " ORDER BY created_at LIMIT 1"
        with self._lock, self._connection:
            row = self._connection.execute(query, parameters).fetchone()
            if row is None:
                return None
            task_project_id, claim_id = str(row["project_id"]), str(row["claim_id"])
            cursor = self._connection.execute(
                """UPDATE coordination_tasks
                SET status = 'leased', leased_by = ?, lease_expires_at = ?, updated_at = ?
                WHERE project_id = ? AND claim_id = ?
                  AND (status = 'pending' OR (status = 'leased' AND lease_expires_at <= ?))""",
                (worker_id, expires_at, now, task_project_id, claim_id, now),
            )
            if cursor.rowcount != 1:
                return None
        return task_project_id, claim_id

    def complete_coordination(
        self,
        project_id: str,
        claim_id: str,
        worker_id: str | None = None,
        now: str | None = None,
    ) -> None:
        now = now or _now()
        if worker_id is None:
            condition = "status IN ('pending', 'leased')"
            parameters: tuple[object, ...] = (now, project_id, claim_id)
        else:
            condition = "status = 'leased' AND leased_by = ? AND lease_expires_at > ?"
            parameters = (now, project_id, claim_id, worker_id, now)
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """UPDATE coordination_tasks SET status = 'completed', updated_at = ?
                , leased_by = NULL, lease_expires_at = NULL
                WHERE project_id = ? AND claim_id = ? AND """ + condition,
                parameters,
            )
            if cursor.rowcount != 1:
                raise ValueError(
                    f"claim {project_id}:{claim_id} has no completable coordination task"
                )

    def validate_coordination_lease(
        self, project_id: str, claim_id: str, worker_id: str, now: str | None = None
    ) -> None:
        now = now or _now()
        with self._lock:
            row = self._connection.execute(
                """SELECT 1 FROM coordination_tasks
                WHERE project_id = ? AND claim_id = ? AND status = 'leased'
                  AND leased_by = ? AND lease_expires_at > ?""",
                (project_id, claim_id, worker_id, now),
            ).fetchone()
        if row is None:
            raise ValueError(
                f"claim {project_id}:{claim_id} is not leased to worker {worker_id}"
            )

    def record_execution_start(
        self, project_id: str, claim_id: str, ts: str
    ) -> None:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """UPDATE claims SET executing_since = ?, last_heartbeat = NULL,
                updated_at = ?
                WHERE project_id = ? AND claim_id = ?""",
                (ts, _now(), project_id, claim_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"{project_id}:{claim_id}")

    def update_progress(
        self, project_id: str, claim_id: str, progress: str, ts: str
    ) -> None:
        with self._lock, self._connection:
            cursor = self._connection.execute(
                """UPDATE claims SET last_heartbeat = ?, progress = ?, updated_at = ?
                WHERE project_id = ? AND claim_id = ?""",
                (ts, progress, _now(), project_id, claim_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"{project_id}:{claim_id}")

    def publish_handoff(
        self, project_id: str, claim_id: str, handoff: Handoff, ts: str
    ) -> None:
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT handoff FROM claims WHERE project_id = ? AND claim_id = ?",
                (project_id, claim_id),
            ).fetchone()
            if row is None:
                raise KeyError(f"{project_id}:{claim_id}")
            encoded = json.dumps(handoff.to_mapping())
            if row["handoff"] and row["handoff"] != encoded:
                raise ValueError("handoff is immutable once published")
            self._connection.execute(
                """UPDATE claims SET handoff = ?, last_heartbeat = ?, updated_at = ?
                WHERE project_id = ? AND claim_id = ?""",
                (encoded, ts, _now(), project_id, claim_id),
            )

    def coordination_created_at(
        self, project_id: str, claim_id: str
    ) -> str | None:
        with self._lock:
            row = self._connection.execute(
                """SELECT created_at FROM coordination_tasks
                WHERE project_id = ? AND claim_id = ?
                  AND status IN ('pending', 'leased')""",
                (project_id, claim_id),
            ).fetchone()
        return str(row["created_at"]) if row else None

    def list_active_all(self) -> list[ClaimRecord]:
        values = tuple(state.value for state in ACTIVE_STATES)
        placeholders = ",".join("?" for _ in values)
        with self._lock:
            rows = self._connection.execute(
                f"SELECT * FROM claims WHERE state IN ({placeholders}) "
                "ORDER BY updated_at",
                values,
            ).fetchall()
        return [self._decode(row) for row in rows]

    def record_subgoal_event(self, event: SubgoalEvent) -> SubgoalEvent:
        created = event.created_at or _now()
        with self._lock, self._connection:
            self._connection.execute("INSERT INTO subgoal_events(project_id, claim_id, payload, created_at) VALUES (?, ?, ?, ?)", (event.project_id, event.claim_id, json.dumps(event.to_mapping()), created))
        return SubgoalEvent.from_mapping({**event.to_mapping(), "created_at": created})

    def list_subgoal_events(self, project_id: str, claim_id: str | None = None) -> list[SubgoalEvent]:
        query = "SELECT payload, created_at FROM subgoal_events WHERE project_id = ?"
        args: list[object] = [project_id]
        if claim_id:
            query += " AND claim_id = ?"; args.append(claim_id)
        query += " ORDER BY id"
        with self._lock:
            rows = self._connection.execute(query, args).fetchall()
        return [SubgoalEvent.from_mapping({**json.loads(row["payload"]), "created_at": row["created_at"]}) for row in rows]

    def publish_coordination_advice(self, advice: CoordinationAdvice) -> CoordinationAdvice:
        created = advice.created_at or _now()
        with self._lock, self._connection:
            self._connection.execute("INSERT INTO coordination_advice(project_id, claim_id, payload, created_at) VALUES (?, ?, ?, ?)", (advice.project_id, advice.claim_id, json.dumps(advice.to_mapping()), created))
        return CoordinationAdvice.from_mapping({**advice.to_mapping(), "created_at": created})

    def list_coordination_advice(self, project_id: str, claim_id: str) -> list[CoordinationAdvice]:
        with self._lock:
            rows = self._connection.execute("SELECT payload, created_at FROM coordination_advice WHERE project_id = ? AND claim_id = ? ORDER BY id", (project_id, claim_id)).fetchall()
        return [CoordinationAdvice.from_mapping({**json.loads(row["payload"]), "created_at": row["created_at"]}) for row in rows]

    @staticmethod
    def _decode(row: sqlite3.Row) -> ClaimRecord:
        decision = json.loads(row["decision"]) if row["decision"] else None
        handoff = json.loads(row["handoff"]) if row["handoff"] else None
        return ClaimRecord(
            claim=Claim.from_mapping(json.loads(row["payload"])),
            state=ClaimState(row["state"]),
            revision=row["revision"],
            decision=Decision.from_mapping(decision) if decision else None,
            updated_at=row["updated_at"],
            executing_since=row["executing_since"] or "",
            last_heartbeat=row["last_heartbeat"] or "",
            progress=row["progress"] or "",
            handoff=Handoff.from_mapping(handoff) if handoff else None,
        )


def build_store():
    """Use Postgres when DATABASE_URL is set, otherwise local SQLite."""
    import os

    database_url = os.getenv("DATABASE_URL", "").strip()
    if database_url:
        from .postgres_store import PostgresClaimStore

        return PostgresClaimStore(database_url)
    return ClaimStore(Path(os.getenv("TCA_DATABASE_PATH", "tca.db")))
