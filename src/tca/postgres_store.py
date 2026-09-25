from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

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
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


class PostgresClaimStore:
    """Transaction-backed Claim store for PostgreSQL-compatible services."""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self._initialize()

    def _connect(self):
        return psycopg.connect(self.database_url, row_factory=dict_row)

    def _initialize(self) -> None:
        statements = (
            """CREATE TABLE IF NOT EXISTS claims (
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
            )""",
            """CREATE TABLE IF NOT EXISTS coordination_tasks (
                project_id TEXT NOT NULL,
                claim_id TEXT NOT NULL,
                claim_revision INTEGER NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                leased_by TEXT,
                lease_expires_at TEXT,
                PRIMARY KEY (project_id, claim_id)
            )""",
            "DROP INDEX IF EXISTS claims_project_state",
            "CREATE INDEX claims_project_state ON claims(project_id, state)",
            "DROP INDEX IF EXISTS coordination_tasks_pending",
            """CREATE INDEX IF NOT EXISTS coordination_tasks_pending
                ON coordination_tasks(status, lease_expires_at, created_at)""",
            """CREATE TABLE IF NOT EXISTS subgoal_events (
                id BIGSERIAL PRIMARY KEY, project_id TEXT NOT NULL,
                claim_id TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS coordination_advice (
                id BIGSERIAL PRIMARY KEY, project_id TEXT NOT NULL,
                claim_id TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL
            )""",
            "CREATE INDEX IF NOT EXISTS subgoal_events_project_claim ON subgoal_events(project_id, claim_id, id)",
            "CREATE INDEX IF NOT EXISTS coordination_advice_project_claim ON coordination_advice(project_id, claim_id, id)",
        )
        with self._connect() as connection:
            with connection.cursor() as cursor:
                for statement in statements:
                    cursor.execute(statement)
                for column in (
                    "executing_since",
                    "last_heartbeat",
                    "progress",
                    "handoff",
                ):
                    cursor.execute(
                        sql.SQL(
                            "ALTER TABLE claims ADD COLUMN IF NOT EXISTS {} TEXT"
                        ).format(sql.Identifier(column))
                    )
                for column in ("leased_by", "lease_expires_at"):
                    cursor.execute(
                        sql.SQL(
                            "ALTER TABLE coordination_tasks ADD COLUMN IF NOT EXISTS {} TEXT"
                        ).format(sql.Identifier(column))
                    )
                self._ensure_primary_key(cursor, "claims")
                self._ensure_primary_key(cursor, "coordination_tasks")

    def _ensure_primary_key(self, cursor, table: str) -> None:
        rows = cursor.execute(
            """SELECT tc.constraint_name, kcu.column_name
            FROM information_schema.table_constraints AS tc
            JOIN information_schema.key_column_usage AS kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.table_schema = kcu.table_schema
            WHERE tc.table_schema = current_schema()
              AND tc.table_name = %s
              AND tc.constraint_type = 'PRIMARY KEY'
            ORDER BY kcu.ordinal_position""",
            (table,),
        ).fetchall()
        columns = tuple(row["column_name"] for row in rows)
        if columns == ("project_id", "claim_id"):
            return
        if rows:
            constraint = rows[0]["constraint_name"]
            cursor.execute(
                sql.SQL("ALTER TABLE {} DROP CONSTRAINT {}").format(
                    sql.Identifier(table),
                    sql.Identifier(constraint),
                )
            )
        cursor.execute(
            sql.SQL("ALTER TABLE {} ADD PRIMARY KEY (project_id, claim_id)").format(
                sql.Identifier(table)
            )
        )

    def close(self) -> None:
        return None

    def save_received(self, claim: Claim) -> ClaimRecord:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT claim_version, state, revision FROM claims
                WHERE project_id = %s AND claim_id = %s FOR UPDATE""",
                (claim.project_id, claim.claim_id),
            ).fetchone()
            now = _now()
            if row is None:
                if claim.version != 1:
                    raise ValueError(
                        f"new claim {claim.claim_id} must start at version 1"
                    )
                revision = 1
                connection.execute(
                    """INSERT INTO claims
                    (project_id, claim_id, claim_version, state, revision, payload,
                     decision, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, NULL, %s)""",
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
                connection.execute(
                    """UPDATE claims SET claim_version = %s, state = %s, revision = %s,
                    payload = %s, decision = NULL, updated_at = %s
                    WHERE project_id = %s AND claim_id = %s""",
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
        with self._connect() as connection:
            row = connection.execute(
                """SELECT state, revision FROM claims
                WHERE project_id = %s AND claim_id = %s FOR UPDATE""",
                (project_id, claim_id),
            ).fetchone()
            if row is None:
                raise KeyError(f"{project_id}:{claim_id}")
            current = ClaimState(row["state"])
            if current not in expected:
                raise ValueError(
                    f"illegal transition for {claim_id}: {current.value} -> {target.value}"
                )
            encoded_decision = json.dumps(decision.to_mapping()) if decision else None
            connection.execute(
                """UPDATE claims SET state = %s, revision = %s,
                decision = COALESCE(%s, decision), updated_at = %s
                WHERE project_id = %s AND claim_id = %s""",
                (
                    target.value,
                    row["revision"] + 1,
                    encoded_decision,
                    _now(),
                    project_id,
                    claim_id,
                ),
            )
        return self.get(project_id, claim_id)

    def get(self, project_id: str, claim_id: str) -> ClaimRecord:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM claims WHERE project_id = %s AND claim_id = %s",
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
        placeholders = ",".join("%s" for _ in values)
        query = (
            f"SELECT * FROM claims WHERE project_id = %s "
            f"AND state IN ({placeholders})"
        )
        parameters: list[object] = [project_id, *values]
        if exclude is not None:
            query += " AND claim_id != %s"
            parameters.append(exclude)
        query += " ORDER BY updated_at"
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [self._decode(row) for row in rows]

    def queue_coordination(
        self, project_id: str, claim_id: str, now: str | None = None
    ) -> None:
        record = self.get(project_id, claim_id)
        now = now or _now()
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO coordination_tasks
                (project_id, claim_id, claim_revision, status, created_at, updated_at,
                 leased_by, lease_expires_at)
                VALUES (%s, %s, %s, 'pending', %s, %s, NULL, NULL)
                ON CONFLICT(project_id, claim_id) DO UPDATE SET
                    claim_revision = EXCLUDED.claim_revision,
                    status = 'pending',
                    created_at = EXCLUDED.created_at,
                    updated_at = EXCLUDED.updated_at,
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
            query += " AND project_id = %s"
            parameters.append(project_id)
        query += " ORDER BY created_at LIMIT 1"
        with self._connect() as connection:
            row = connection.execute(query, parameters).fetchone()
        return (str(row["project_id"]), str(row["claim_id"])) if row else None

    def claim_coordination(
        self,
        project_id: str | None,
        worker_id: str,
        lease_seconds: float,
        now: str | None = None,
    ) -> tuple[str, str] | None:
        if not worker_id.strip():
            raise ValueError("worker_id is required to lease a coordination task")
        now = now or _now()
        expires_at = (
            datetime.fromisoformat(now) + timedelta(seconds=max(1.0, lease_seconds))
        ).isoformat()
        scope = ""
        parameters: list[object] = [now]
        if project_id:
            scope = " AND project_id = %s"
            parameters.append(project_id)
        parameters.extend([worker_id, expires_at, now])
        with self._connect() as connection:
            row = connection.execute(
                f"""WITH candidate AS (
                    SELECT project_id, claim_id FROM coordination_tasks
                    WHERE (status = 'pending' OR (status = 'leased' AND lease_expires_at <= %s))
                    {scope}
                    ORDER BY created_at
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                UPDATE coordination_tasks AS task
                SET status = 'leased', leased_by = %s, lease_expires_at = %s,
                    updated_at = %s
                FROM candidate
                WHERE task.project_id = candidate.project_id
                  AND task.claim_id = candidate.claim_id
                RETURNING task.project_id, task.claim_id""",
                parameters,
            ).fetchone()
        return (str(row["project_id"]), str(row["claim_id"])) if row else None

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
            condition = "status = 'leased' AND leased_by = %s AND lease_expires_at > %s"
            parameters = (now, project_id, claim_id, worker_id, now)
        with self._connect() as connection:
            cursor = connection.execute(
                """UPDATE coordination_tasks SET status = 'completed', updated_at = %s
                , leased_by = NULL, lease_expires_at = NULL
                WHERE project_id = %s AND claim_id = %s AND """ + condition,
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
        with self._connect() as connection:
            row = connection.execute(
                """SELECT 1 FROM coordination_tasks
                WHERE project_id = %s AND claim_id = %s AND status = 'leased'
                  AND leased_by = %s AND lease_expires_at > %s""",
                (project_id, claim_id, worker_id, now),
            ).fetchone()
        if row is None:
            raise ValueError(
                f"claim {project_id}:{claim_id} is not leased to worker {worker_id}"
            )

    def record_execution_start(
        self, project_id: str, claim_id: str, ts: str
    ) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                """UPDATE claims SET executing_since = %s, last_heartbeat = NULL,
                updated_at = %s
                WHERE project_id = %s AND claim_id = %s""",
                (ts, _now(), project_id, claim_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"{project_id}:{claim_id}")

    def update_progress(
        self, project_id: str, claim_id: str, progress: str, ts: str
    ) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                """UPDATE claims SET last_heartbeat = %s, progress = %s,
                updated_at = %s
                WHERE project_id = %s AND claim_id = %s""",
                (ts, progress, _now(), project_id, claim_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"{project_id}:{claim_id}")

    def publish_handoff(
        self, project_id: str, claim_id: str, handoff: Handoff, ts: str
    ) -> None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT handoff FROM claims WHERE project_id = %s AND claim_id = %s FOR UPDATE",
                (project_id, claim_id),
            ).fetchone()
            if row is None:
                raise KeyError(f"{project_id}:{claim_id}")
            encoded = json.dumps(handoff.to_mapping())
            if row.get("handoff") and row["handoff"] != encoded:
                raise ValueError("handoff is immutable once published")
            connection.execute(
                """UPDATE claims SET handoff = %s, last_heartbeat = %s, updated_at = %s
                WHERE project_id = %s AND claim_id = %s""",
                (encoded, ts, _now(), project_id, claim_id),
            )

    def coordination_created_at(
        self, project_id: str, claim_id: str
    ) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT created_at FROM coordination_tasks
                WHERE project_id = %s AND claim_id = %s
                  AND status IN ('pending', 'leased')""",
                (project_id, claim_id),
            ).fetchone()
        return str(row["created_at"]) if row else None

    def list_active_all(self) -> list[ClaimRecord]:
        values = tuple(state.value for state in ACTIVE_STATES)
        placeholders = ",".join("%s" for _ in values)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM claims WHERE state IN ({placeholders}) "
                "ORDER BY updated_at",
                values,
            ).fetchall()
        return [self._decode(row) for row in rows]

    def record_subgoal_event(self, event: SubgoalEvent) -> SubgoalEvent:
        created = event.created_at or _now()
        with self._connect() as connection:
            connection.execute("INSERT INTO subgoal_events(project_id, claim_id, payload, created_at) VALUES (%s, %s, %s, %s)", (event.project_id, event.claim_id, json.dumps(event.to_mapping()), created))
        return SubgoalEvent.from_mapping({**event.to_mapping(), "created_at": created})

    def list_subgoal_events(self, project_id: str, claim_id: str | None = None) -> list[SubgoalEvent]:
        query, args = "SELECT payload, created_at FROM subgoal_events WHERE project_id = %s", [project_id]
        if claim_id:
            query += " AND claim_id = %s"; args.append(claim_id)
        with self._connect() as connection:
            rows = connection.execute(query + " ORDER BY id", args).fetchall()
        return [SubgoalEvent.from_mapping({**json.loads(row["payload"]), "created_at": row["created_at"]}) for row in rows]

    def publish_coordination_advice(self, advice: CoordinationAdvice) -> CoordinationAdvice:
        created = advice.created_at or _now()
        with self._connect() as connection:
            connection.execute("INSERT INTO coordination_advice(project_id, claim_id, payload, created_at) VALUES (%s, %s, %s, %s)", (advice.project_id, advice.claim_id, json.dumps(advice.to_mapping()), created))
        return CoordinationAdvice.from_mapping({**advice.to_mapping(), "created_at": created})

    def list_coordination_advice(self, project_id: str, claim_id: str) -> list[CoordinationAdvice]:
        with self._connect() as connection:
            rows = connection.execute("SELECT payload, created_at FROM coordination_advice WHERE project_id = %s AND claim_id = %s ORDER BY id", (project_id, claim_id)).fetchall()
        return [CoordinationAdvice.from_mapping({**json.loads(row["payload"]), "created_at": row["created_at"]}) for row in rows]

    @staticmethod
    def _decode(row: dict[str, Any]) -> ClaimRecord:
        decision = json.loads(row["decision"]) if row["decision"] else None
        handoff = json.loads(row["handoff"]) if row.get("handoff") else None
        return ClaimRecord(
            claim=Claim.from_mapping(json.loads(row["payload"])),
            state=ClaimState(row["state"]),
            revision=row["revision"],
            decision=Decision.from_mapping(decision) if decision else None,
            updated_at=row["updated_at"],
            executing_since=row.get("executing_since") or "",
            last_heartbeat=row.get("last_heartbeat") or "",
            progress=row.get("progress") or "",
            handoff=Handoff.from_mapping(handoff) if handoff else None,
        )
