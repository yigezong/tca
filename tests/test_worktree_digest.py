import json
import subprocess

from tca.controller import Controller
from tca.domain import ClaimState, Decision
from tca.rules import BasicRuleChecker
from tca.store import ClaimStore
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
        "    def reserve(self, sku, quantity, order_id):\n"
        "        secret_business_logic = 'do not transmit'\n"
        "        return secret_business_logic\n"
    )
    (repo / "secret.txt").write_text("private unrelated material")
    digest = build_worktree_digest(repo, {"planned_paths": ["src/inventory.py"]})

    assert digest["changed_paths"] == ["src/inventory.py"]
    assert digest["outside_scope_count"] == 1
    assert any("order_id" in change for change in digest["api_changes"])
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
        "outside_scope_count": 0, "truncated": False,
        "summary": "Reservation is now idempotent by order_id.",
    }
    controller.report_worktree_snapshot("team/project", "a1", snapshot)
    controller.submit_claim(make_claim("a2", ("src/inventory.py", "src/checkout.py"), coordination_group=group))

    task = controller.get_coordination_task("team/project")
    assert task is not None
    peer = next(item for item in task.active_claims if item.claim.claim_id == "a1")
    assert peer.worktree_snapshot is not None
    assert peer.worktree_snapshot["api_changes"] == snapshot["api_changes"]
    assert peer.worktree_snapshot["summary"] == snapshot["summary"]
    assert peer.worktree_snapshot["observed_at"]

    controller.report_worktree_snapshot("team/project", "a1", {**snapshot, "summary": ""})
    assert controller.store.get("team/project", "a1").worktree_snapshot["summary"] == snapshot["summary"]
    controller.report_worktree_snapshot("team/project", "a1", {
        **snapshot, "fingerprint": "c" * 64, "summary": "",
    })
    assert controller.store.get("team/project", "a1").worktree_snapshot["summary"] == ""
