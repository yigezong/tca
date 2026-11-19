import argparse
import json
import subprocess
import sys

import pytest

from tca.agent_adapters import install_adapter
from tca.codex_setup import SetupError, setup
from tca.controller import Controller
from tca.domain import ClaimState, Decision
from tca.rules import BasicRuleChecker
from tca.store import ClaimStore

from test_controller import make_claim


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.mark.parametrize("client", ["pi", "opencode", "dsh"])
def test_adapter_config_has_mcp_and_turn_capture(tmp_path, monkeypatch, client):
    repo = tmp_path / client
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    monkeypatch.setattr("tca.codex_setup.shutil.which", lambda _: "/usr/bin/true")
    if client == "opencode":
        monkeypatch.setattr("tca.agent_adapters._opencode_major", lambda: 2)
    args = argparse.Namespace(repo=repo, agent_id="agent-a1", project_id="team/project",
                              mode="local", server_url="", token_env="TCA_TOKEN",
                              replace_mcp=False, client=client)
    setup(args)
    setup(args)
    assert "project_id: team/project" in (repo / ".tca/project.yaml").read_text()
    assert "tca.capture_cli" in {
        "pi": repo / ".pi/extensions/tca.ts",
        "opencode": repo / ".opencode/plugins/tca/index.ts",
        "dsh": repo / ".tca/dsh-hooks.json",
    }[client].read_text()
    assert "tca.local_mcp" in {
        "pi": repo / ".pi/mcp.json",
        "opencode": repo / ".opencode/opencode.json",
        "dsh": repo / ".tca/dsh.patch.yml",
    }[client].read_text()
    assert "TCA" in (repo / "AGENTS.md").read_text()
    assert ".tca/agent-id" in (repo / ".gitignore").read_text()


def test_remote_configs_reference_token_env_not_secret(tmp_path):
    for client in ("pi", "opencode", "dsh"):
        repo = tmp_path / client
        repo.mkdir()
        install_adapter(client, repo, "tca", {"type": "streamable_http",
                        "url": "https://example.invalid/mcp", "bearer_token_env_var": "TCA_TOKEN"})
        encoded = "\n".join(path.read_text() for path in repo.rglob("*") if path.is_file())
        assert "TCA_TOKEN" in encoded
        assert "tca_secret-session" not in encoded


def test_opencode_v1_uses_compatible_plugin(tmp_path, monkeypatch):
    monkeypatch.setattr("tca.agent_adapters._opencode_major", lambda: 1)
    repo = tmp_path / "opencode"
    repo.mkdir()
    install_adapter("opencode", repo, "tca_local",
                    {"type": "stdio", "command": sys.executable, "args": ["-m", "tca.local_mcp"]})
    config = json.loads((repo / ".opencode/opencode.json").read_text())
    assert config["plugin"] == ["./plugin/tca.ts"]
    assert "session.idle" in (repo / ".opencode/plugin/tca.ts").read_text()


def test_manager_requires_codex_before_writing_files(tmp_path, monkeypatch):
    repo = tmp_path / "manager"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    with pytest.raises(SetupError, match="requires Codex"):
        setup(argparse.Namespace(repo=repo, agent_id="agent-m", client="pi"))
    assert not (repo / ".tca").exists()


def test_cross_client_capture_uses_same_controller_state(tmp_path, monkeypatch):
    monkeypatch.setenv("TCA_HOME", str(tmp_path / "home"))
    home = tmp_path / "home"
    home.mkdir()
    db = home / "coordination.db"
    store = ClaimStore(db)
    controller = Controller(store, BasicRuleChecker())
    controller.submit_claim(make_claim("a1", ("src/inventory.py",), coordination_group="demo"))
    controller.submit_coordination_decision("team/project", "a1",
                                            Decision(ClaimState.APPROVED, "reviewed"))
    controller.start_execution("team/project", "a1")
    repo = tmp_path / "developer"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Tester")
    _git(repo, "config", "user.email", "tester@example.invalid")
    source = repo / "src/inventory.py"
    source.parent.mkdir()
    source.write_text("def reserve(sku):\n    return True\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "baseline")
    _git(repo, "checkout", "-b", "feature/agent-a1/a1")
    (repo / ".tca").mkdir()
    (repo / ".tca/project.yaml").write_text(
        "project_id: team/project\nmode: local\nmcp: {server: tca_local}\n")
    (repo / ".tca/agent-id").write_text("agent-a1\n")
    source.write_text("def reserve(sku, order_id):\n    secret = 'private source'\n    return secret\n")
    for turn in ("pi:1", "opencode:2", "dsh:3"):
        subprocess.run([sys.executable, "-m", "tca.capture_cli", "--repo", str(repo),
                        "--turn-id", turn], check=True, capture_output=True, text=True)
    snapshot = store.get("team/project", "a1").worktree_snapshot
    assert snapshot["sequence"] == 3
    assert "order_id" in json.dumps(snapshot)
    assert "private source" not in json.dumps(snapshot)
    controller.submit_claim(make_claim("a2", ("src/inventory.py",), coordination_group="demo"))
    task = controller.get_coordination_task("team/project")
    assert task is not None
    assert any(item.worktree_snapshot and item.worktree_snapshot["sequence"] == 3
               for item in task.active_claims if item.claim.claim_id == "a1")
