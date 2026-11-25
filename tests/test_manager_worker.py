from pathlib import Path

import pytest

from tca.codex_setup import SetupError, _session_path
from tca.controller import Controller
from tca.domain import Claim
from tca.manager_worker import analyze, process_one, project, session_token
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


@pytest.mark.anyio
@pytest.mark.parametrize("client", ["pi", "opencode", "dsh"])
async def test_resident_m_other_clients_commit_decision_through_c(tmp_path: Path, monkeypatch,
                                                                      client: str) -> None:
    import json

    monkeypatch.setenv("TCA_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    (repo / ".tca").mkdir(parents=True)
    (repo / ".tca" / "agent-id").write_text("agent-m\n")
    (repo / ".tca" / "project.yaml").write_text(
        "version: 1\nproject_id: team/project\nmode: local\nmcp:\n  server: tca_local\n")
    database = tmp_path / "home" / "coordination.db"
    database.parent.mkdir()
    controller = Controller(ClaimStore(database), BasicRuleChecker())
    controller.submit_claim(Claim(
        claim_id="a1", project_id="team/project", author="agent-a1",
        base_commit="abc123", work_branch="feature/agent-a1/a1", goal="add inventory",
        planned_paths=("src/inventory.py",), coordination_group="checkout",
    ))
    fake = tmp_path / client
    fake.write_text("""#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
if Path(sys.argv[0]).name == 'pi':
    assert '--no-tools' in args and '--no-mcp' in args
    prompt = args[-1]
elif Path(sys.argv[0]).name == 'opencode':
    assert args[:4] == ['run', '--pure', '--agent', 'plan']
    prompt = args[-1]
else:
    if '--help' in args:
        print('--permission-mode --approval-policy')
        raise SystemExit(0)
    assert '--permission-mode' in args and 'read-only' in args and '--patch' in args
    assert os.environ.get('DSH_PERMISSION_MODE') == 'read-only'
    prompt = sys.stdin.read()
assert 'add inventory' in prompt
assert 'TCA_TOKEN' not in os.environ
print(json.dumps({'advice': 'Own inventory.py; keep reserve stable.',
    'state': 'approved_with_constraints', 'reason': 'Clear ownership',
    'constraints': ['Keep reserve stable'], 'wait_for': [], 'conflicts_with': [],
    'boundary_contract': 'reserve(sku) -> bool'}))
""")
    fake.chmod(0o700)
    monkeypatch.setenv("TCA_TOKEN", "tca_secret-do-not-pass")
    assert await process_one(repo, project(repo), str(fake), timeout=0, client=client)
    assert controller.get_claim_state("team/project", "a1")["state"] == "approved_with_constraints"
    advice = controller.get_integration_context("team/project", "a1")["advice"]
    assert advice[0]["message"] == "Own inventory.py; keep reserve stable."


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


def test_launchd_uses_selected_m_client_without_embedding_token(tmp_path: Path, monkeypatch) -> None:
    import plistlib
    from tca import manager_service

    repo = tmp_path / "repo"
    (repo / ".tca").mkdir(parents=True)
    (repo / ".tca/manager-client").write_text("pi\n")
    monkeypatch.setattr(manager_service.sys, "platform", "darwin")
    monkeypatch.setattr(manager_service.shutil, "which", lambda _: "/usr/local/bin/pi")
    monkeypatch.setattr(manager_service, "paths", lambda _: (tmp_path / "m.plist", tmp_path / "m.log"))
    monkeypatch.setattr(manager_service, "running", lambda _: False)
    monkeypatch.setenv("TCA_TOKEN", "tca_secret-do-not-save")
    monkeypatch.setattr(manager_service.subprocess, "run", lambda *a, **k:
                        type("Result", (), {"returncode": 0})())
    manager_service.start(repo)
    content = (tmp_path / "m.plist").read_bytes()
    arguments = plistlib.loads(content)["ProgramArguments"]
    assert arguments[-4:] == ["--client", "pi", "--client-bin", "/usr/local/bin/pi"]
    assert b"tca_secret-do-not-save" not in content


def test_dsh_older_headless_cli_uses_read_only_environment(tmp_path: Path) -> None:
    fake = tmp_path / "dsh"
    fake.write_text("""#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
if '--help' in sys.argv:
    print('Usage: dsh --profile headless [task...]')
    raise SystemExit(0)
assert '--permission-mode' not in sys.argv
assert '--patch' in sys.argv
assert 'disabled: false' in Path(sys.argv[sys.argv.index('--patch')+1]).read_text()
assert os.environ['DSH_PERMISSION_MODE'] == 'read-only'
assert 'smoke' in sys.stdin.read()
print(json.dumps({'advice':'Own reserve.', 'state':'approved', 'reason':'Separate scope',
 'constraints':[], 'wait_for':[], 'conflicts_with':[], 'boundary_contract':''}))
""")
    fake.chmod(0o700)
    answer = analyze(tmp_path, {"claim": {"claim": {"claim_id": "smoke"}}},
                     str(fake), client="dsh")
    assert answer["state"] == "approved"
