from pathlib import Path

import pytest

from tca.codex_setup import SetupError, _session_path
from tca.controller import Controller
from tca.domain import Claim
from tca.manager_worker import process_one, project, session_token
from tca.rules import BasicRuleChecker
from tca.store import ClaimStore


@pytest.mark.anyio
async def test_resident_m_handles_claim_through_real_local_mcp(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("TCA_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    (repo / ".tca").mkdir(parents=True)
    (repo / ".tca" / "agent-id").write_text("agent-m\n")
    (repo / ".tca" / "project.yaml").write_text(
        "version: 1\nproject_id: team/project\nmode: local\nmcp:\n  server: tca_local\n"
    )
    database = tmp_path / "home" / "coordination.db"
    database.parent.mkdir()
    store = ClaimStore(database)
    controller = Controller(store, BasicRuleChecker())
    controller.submit_claim(Claim(
        claim_id="a1", project_id="team/project", author="agent-a1",
        base_commit="abc123", work_branch="feature/agent-a1/a1", goal="add inventory",
        planned_paths=("src/inventory.py",), coordination_group="checkout",
    ))

    fake_codex = tmp_path / "codex"
    fake_codex.write_text("""#!/usr/bin/env python3
import json, sys
from pathlib import Path
prompt = sys.stdin.read()
assert 'add inventory' in prompt
output = Path(sys.argv[sys.argv.index('--output-last-message') + 1])
output.write_text(json.dumps({
    'advice': 'Own inventory.py; keep reserve(stock_id) stable.',
    'state': 'approved_with_constraints', 'reason': 'Interface ownership is clear',
    'constraints': ['Keep reserve(stock_id) stable'], 'wait_for': [],
    'conflicts_with': [], 'boundary_contract': 'reserve(stock_id) -> bool'
}))
""")
    fake_codex.chmod(0o700)

    assert await process_one(repo, project(repo), str(fake_codex), timeout=0)
    state = controller.get_claim_state("team/project", "a1")
    assert state["state"] == "approved_with_constraints"
    advice = controller.get_integration_context("team/project", "a1")["advice"]
    assert advice[0]["message"] == "Own inventory.py; keep reserve(stock_id) stable."
    assert not await process_one(repo, project(repo), str(fake_codex), timeout=0)


def test_resident_m_rejects_a_worktree(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / ".tca").mkdir(parents=True)
    (repo / ".tca" / "agent-id").write_text("agent-a1\n")
    (repo / ".tca" / "project.yaml").write_text("mode: local\n")
    with pytest.raises(SetupError, match="agent-m"):
        project(repo)


def test_resident_m_renews_expiring_session_with_original_key(tmp_path: Path, monkeypatch) -> None:
    import json

    monkeypatch.setenv("TCA_HOME", str(tmp_path / "home"))
    server = "https://tca.example/mcp"
    path = _session_path(server)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"token": "tca_old", "login": "alice",
                                "expires_at": "2000-01-01T00:00:00+00:00",
                                "key_path": str(tmp_path / "alice-key")}))
    seen = []

    def refresh(args):
        seen.append((args.login, args.key_path))
        path.write_text(json.dumps({"token": "tca_new", "login": "alice",
                                    "expires_at": "2099-01-01T00:00:00+00:00"}))

    monkeypatch.setattr("tca.manager_worker.login_command", refresh)
    assert session_token(server, "TCA_TOKEN") == "tca_new"
    assert seen == [("alice", tmp_path / "alice-key")]
