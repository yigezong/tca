import sqlite3

from tca.domain import Claim, ClaimState
from tca.store import ClaimStore


def make_claim(project_id: str, claim_id: str, version: int = 1) -> Claim:
    return Claim(
        claim_id=claim_id,
        project_id=project_id,
        author="agent-a",
        base_commit="abc123",
        work_branch=f"feature/{claim_id}",
        goal="make a coordinated change",
        planned_paths=("src/example/**",),
        version=version,
    )


def test_sqlite_store_migrates_global_claim_id_primary_key(tmp_path) -> None:
    database = tmp_path / "tca.db"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE claims (
                claim_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                claim_version INTEGER NOT NULL,
                state TEXT NOT NULL,
                revision INTEGER NOT NULL,
                payload TEXT NOT NULL,
                decision TEXT,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE coordination_tasks (
                claim_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                claim_revision INTEGER NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )

    store = ClaimStore(database)
    try:
        store.save_received(make_claim("team/project-a", "claim-1"))
        store.save_received(make_claim("team/project-b", "claim-1"))

        assert store.get("team/project-a", "claim-1").claim.project_id == "team/project-a"
        assert store.get("team/project-b", "claim-1").claim.project_id == "team/project-b"
    finally:
        store.close()


def test_new_claim_must_start_at_version_one() -> None:
    store = ClaimStore()
    try:
        try:
            store.save_received(make_claim("team/project", "claim-1", version=2))
        except ValueError as error:
            assert "must start at version 1" in str(error)
        else:
            raise AssertionError("new claim version 2 should be rejected")
    finally:
        store.close()


def test_claim_history_retains_revisions_and_state_transitions() -> None:
    store = ClaimStore()
    try:
        first = make_claim("team/project", "claim-1")
        store.save_received(first)
        store.save_received(make_claim("team/project", "claim-1", version=2))
        store.transition("team/project", "claim-1", {ClaimState.RECEIVED}, ClaimState.APPROVED)
        revisions = store.list_claim_revisions("team/project", "claim-1")
        events = store.list_claim_events("team/project", "claim-1")
        assert [item["version"] for item in revisions] == [1, 2]
        assert [item["event_type"] for item in events] == [
            "claim_submitted", "claim_submitted", "state_transition"
        ]
        assert events[-1]["payload"]["to"] == "approved"
    finally:
        store.close()


def test_coordination_lease_excludes_other_workers_and_recovers_after_expiry() -> None:
    store = ClaimStore()
    try:
        claim = make_claim("team/project", "claim-1")
        store.save_received(claim)
        store.queue_coordination(
            claim.project_id, claim.claim_id, now="2026-01-01T00:00:00+00:00"
        )

        assert store.claim_coordination(
            claim.project_id,
            "worker-1",
            30,
            now="2026-01-01T00:00:00+00:00",
        ) == (claim.project_id, claim.claim_id)
        assert store.claim_coordination(
            claim.project_id,
            "worker-2",
            30,
            now="2026-01-01T00:00:29+00:00",
        ) is None
        assert store.claim_coordination(
            claim.project_id,
            "worker-2",
            30,
            now="2026-01-01T00:00:31+00:00",
        ) == (claim.project_id, claim.claim_id)
    finally:
        store.close()


def test_coordination_lease_requires_the_worker_that_claimed_it() -> None:
    store = ClaimStore()
    try:
        claim = make_claim("team/project", "claim-1")
        store.save_received(claim)
        store.queue_coordination(claim.project_id, claim.claim_id)
        assert store.claim_coordination(claim.project_id, "worker-1", 30) is not None

        try:
            store.validate_coordination_lease(
                claim.project_id, claim.claim_id, "worker-2"
            )
        except ValueError as error:
            assert "not leased" in str(error)
        else:
            raise AssertionError("another worker must not decide a leased task")
    finally:
        store.close()
