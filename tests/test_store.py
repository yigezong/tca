import sqlite3

from tca.domain import Claim
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
