import json
import subprocess
import sys

from tca.controller import Controller
from tca.domain import ClaimState, Decision
from tca.rules import BasicRuleChecker
from tca.store import ClaimStore
from tca.turn_hook import capture_turn
from tca.worktree_digest import build_worktree_digest

from test_controller import make_claim


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def test_digest_exposes_only_claimed_names_and_signature_shapes(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Tester")
    _git(repo, "config", "user.email", "tester@example.invalid")
    source = repo / "src" / "inventory.py"
    source.parent.mkdir()
    source.write_text("class Inventory:\n    def reserve(self, sku, quantity):\n        return True\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "baseline")

    source.write_text(
        "class Inventory:\n"
        "    def reserve(self, sku, quantity, order_id: str) -> str:\n"
        "        secret_business_logic = 'do not transmit'\n"
        "        return secret_business_logic\n"
    )
    (repo / "secret.txt").write_text("private unrelated material")
    digest = build_worktree_digest(repo, {"planned_paths": ["src/inventory.py"]})

    assert digest["changed_paths"] == ["src/inventory.py"]
    assert digest["file_changes"] == ["modified: src/inventory.py"]
    assert digest["outside_scope_count"] == 1
    assert any("order_id" in change for change in digest["api_changes"])
    assert any("order_id:str" in change and "->str" in change for change in digest["api_changes"])
    encoded = json.dumps(digest)
    assert "do not transmit" not in encoded
    assert "private unrelated material" not in encoded
    assert "secret.txt" not in encoded


def test_m_task_contains_latest_uncommitted_worktree_digest() -> None:
    controller = Controller(ClaimStore(), BasicRuleChecker())
    group = "demo"
    controller.submit_claim(make_claim("a1", ("src/inventory.py",), coordination_group=group))
    controller.submit_coordination_decision(
        "team/project", "a1",
        Decision(ClaimState.APPROVED, "reviewed"),
    )
    controller.start_execution("team/project", "a1")
    snapshot = {
        "head": "a" * 40, "fingerprint": "b" * 64,
        "changed_paths": ["src/inventory.py"],
        "changed_symbols": [],
        "api_changes": ["src/inventory.py:Inventory.reserve: (self,sku,quantity) -> (self,sku,quantity,order_id)"],
        "outside_scope_count": 0, "truncated": False, "turn_id": "turn-one",
    }
    controller.report_worktree_snapshot("team/project", "a1", snapshot)
    controller.submit_claim(make_claim("a2", ("src/inventory.py", "src/checkout.py"), coordination_group=group))

    task = controller.get_coordination_task("team/project")
    assert task is not None
    peer = next(item for item in task.active_claims if item.claim.claim_id == "a1")
    assert peer.worktree_snapshot is not None
    assert peer.worktree_snapshot["api_changes"] == snapshot["api_changes"]
    assert peer.worktree_snapshot["sequence"] == 1
    assert peer.worktree_snapshot["observed_at"]

    controller.report_worktree_snapshot("team/project", "a1", snapshot)
    assert controller.store.get("team/project", "a1").worktree_snapshot["sequence"] == 1
    controller.report_worktree_snapshot("team/project", "a1", {
        **snapshot, "fingerprint": "c" * 64, "turn_id": "turn-two",
    })
    assert controller.store.get("team/project", "a1").worktree_snapshot["sequence"] == 2


def test_codex_stop_hook_captures_local_uncommitted_changes(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TCA_HOME", str(tmp_path / "tca-home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Tester")
    _git(repo, "config", "user.email", "tester@example.invalid")
    source = repo / "src" / "inventory.py"
    source.parent.mkdir()
    source.write_text("def reserve(sku, quantity):\n    return True\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "baseline")
    _git(repo, "checkout", "-b", "feature/agent-a1/a1")
    config = repo / ".tca"
    config.mkdir()
    (config / "project.yaml").write_text("project_id: team/project\nmode: local\nmcp: {server: tca_local}\n")
    (config / "agent-id").write_text("agent-a1\n")
    from tca.codex_setup import local_db
    local_db().parent.mkdir()
    store = ClaimStore(local_db())
    controller = Controller(store, BasicRuleChecker())
    controller.submit_claim(make_claim("a1", ("src/inventory.py",)))
    controller.start_execution("team/project", "a1")

    source.write_text("def reserve(sku, quantity, order_id):\n    secret = 'private'\n    return secret\n")
    payload = {"hook_event_name": "Stop", "cwd": str(repo), "turn_id": "turn-1",
               "last_assistant_message": "Do not transmit this message"}
    result = capture_turn(payload)
    assert result is not None
    snapshot = controller.store.get("team/project", "a1").worktree_snapshot
    assert snapshot["file_changes"] == ["modified: src/inventory.py"]
    assert "order_id" in str(snapshot["api_changes"])
    assert "private" not in json.dumps(snapshot)
    assert "Do not transmit" not in json.dumps(snapshot)
    capture_turn(payload)
    assert controller.store.get("team/project", "a1").worktree_snapshot["sequence"] == 1
    capture_turn({**payload, "turn_id": "turn-2"})
    assert controller.store.get("team/project", "a1").worktree_snapshot["sequence"] == 2
    completed = subprocess.run(
        [sys.executable, "-m", "tca.turn_hook"],
        input=json.dumps({**payload, "turn_id": "turn-3"}), text=True,
        capture_output=True, cwd=repo, check=True,
    )
    assert completed.stdout.strip() == "{}"
    assert controller.store.get("team/project", "a1").worktree_snapshot["sequence"] == 3
    captures = [event for event in controller.store.list_claim_events("team/project", "a1")
                if event["event_type"] == "worktree_snapshot"]
    assert len(captures) == 3


def test_codex_stop_hook_remote_sends_metadata_only(tmp_path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Tester")
    _git(repo, "config", "user.email", "tester@example.invalid")
    source = repo / "src" / "inventory.py"
    source.parent.mkdir()
    source.write_text("def reserve(sku):\n    return True\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "baseline")
    _git(repo, "checkout", "-b", "feature/agent-a1/a1")
    (repo / ".tca").mkdir()
    (repo / ".tca/project.yaml").write_text(
        "project_id: team/project\nmode: remote\nmcp: {url: 'https://tca.example/mcp', token_env: TCA_TOKEN}\n"
    )
    (repo / ".tca/agent-id").write_text("agent-a1\n")
    source.write_text("def reserve(sku, order_id):\n    secret = 'never upload'\n    return secret\n")
    monkeypatch.setattr("tca.turn_hook._tca_token", lambda url, env: "tca_test")
    calls = []

    def fake_api(url, token, path, body=None):
        calls.append((url, token, path, body))
        if body is None:
            return {"claim": {"state": "executing", "claim": make_claim(
                "a1", ("src/inventory.py",)
            ).to_mapping()}}
        return {"worktree_snapshot": body["snapshot"]}

    monkeypatch.setattr("tca.turn_hook._project_api", fake_api)
    capture_turn({"hook_event_name": "Stop", "cwd": str(repo), "turn_id": "turn-r1",
                  "last_assistant_message": "private conversation"})
    assert len(calls) == 2
    assert calls[0][2].endswith("/turn-capture?branch=feature%2Fagent-a1%2Fa1")
    encoded = json.dumps(calls[1][3])
    assert "order_id" in encoded
    assert "never upload" not in encoded
    assert "private conversation" not in encoded
    assert calls[1][3]["snapshot"]["turn_id"] == "turn-r1"
