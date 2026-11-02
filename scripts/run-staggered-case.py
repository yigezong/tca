"""Reproducible four-developer staggered protocol case with real Git branches.

Actors and M decisions are scripted. This tests state transitions, scope
revision, delivery checks and clean integration, not LLM quality or speedup.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

from tca.completion import verify_local_git
from tca.controller import Controller
from tca.domain import Claim, ClaimState, CoordinationAdvice, Decision
from tca.metrics import claim_metrics, project_metrics
from tca.rules import BasicRuleChecker
from tca.store import ClaimStore


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], text=True,
                            capture_output=True, check=True)
    return result.stdout.strip()


def make_claim(name: str, base: str, paths: tuple[str, ...], *,
               group: str = "", version: int = 1) -> Claim:
    return Claim(
        claim_id=name, project_id="demo/order-flow", author=f"agent-{name}",
        base_commit=base, work_branch=f"feature/agent-{name}/{name}",
        goal={"a1": "normalize IDs", "a2": "ingest normalized orders",
              "a3": "test the data flow", "a4": "report unique orders"}[name],
        planned_paths=paths, coordination_group=group,
        expected_group_size=4 if group else 1, version=version,
    )


def run() -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="tca-staggered-") as directory:
        root = Path(directory)
        remote = root / "remote.git"
        repo = root / "repo"
        subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
        subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
        git(repo, "config", "user.email", "tca@example.test")
        git(repo, "config", "user.name", "TCA Case")
        git(repo, "remote", "add", "origin", str(remote))
        (repo / "order_flow.py").write_text(
            "def normalize_order_id(raw: str) -> str:\n"
            "    raise NotImplementedError\n"
        )
        git(repo, "add", ".")
        git(repo, "commit", "-m", "base")
        base = git(repo, "rev-parse", "HEAD")
        git(repo, "push", "origin", "main")

        controller = Controller(ClaimStore(root / "protocol.db"), BasicRuleChecker())
        a1 = make_claim("a1", base, ("order_flow.py",), group="order-flow")
        a2 = make_claim("a2", base, ("ingest.py",), group="order-flow")
        a3 = make_claim("a3", base, ("order_flow.py", "tests/test_flow.py"), group="order-flow")
        a4 = make_claim("a4", base, ("report.py",))

        # A1 arrives, then A2 arrives before anybody begins work.
        assert controller.submit_claim(a1).state is ClaimState.ASSESSING
        assert controller.submit_claim(a2).state is ClaimState.ASSESSING
        for name, advice in (("a1", "Own normalize_order_id in order_flow.py"),
                             ("a2", "Use normalize_order_id from A1; own ingest.py")):
            controller.publish_coordination_advice(CoordinationAdvice(
                "demo/order-flow", name, "agent-m", advice,
            ))
            controller.submit_coordination_decision(
                "demo/order-flow", name,
                Decision(ClaimState.APPROVED, advice),
            )
        controller.start_execution("demo/order-flow", "a1")
        controller.start_execution("demo/order-flow", "a2")

        # A3 arrives late with an overlapping Claim. M narrows only A3.
        a1_revision = controller.store.get("demo/order-flow", "a1").revision
        assert controller.submit_claim(a3).state is ClaimState.ASSESSING
        assert controller.store.get("demo/order-flow", "a1").revision == a1_revision
        controller.publish_coordination_advice(CoordinationAdvice(
            "demo/order-flow", "a3", "agent-m",
            "A1 owns order_flow.py. Revise your Claim to own tests/test_flow.py only.",
        ))
        a3 = make_claim("a3", base, ("tests/test_flow.py",), group="order-flow", version=2)
        assert controller.submit_claim(a3).state is ClaimState.APPROVED
        controller.start_execution("demo/order-flow", "a3")
        assert controller.submit_claim(a4).state is ClaimState.APPROVED
        controller.start_execution("demo/order-flow", "a4")

        edits = {
            "a1": {"order_flow.py": (
                "def normalize_order_id(raw: str) -> str:\n"
                "    value = raw.strip().upper()\n"
                "    if not value:\n"
                "        raise ValueError('empty order ID')\n"
                "    return value\n"
            )},
            "a2": {"ingest.py": (
                "from order_flow import normalize_order_id\n\n"
                "def ingest(values):\n"
                "    return [normalize_order_id(value) for value in values]\n"
            )},
            "a3": {"tests/test_flow.py": (
                "import unittest\n"
                "from ingest import ingest\n"
                "from report import count_orders\n\n"
                "class FlowTest(unittest.TestCase):\n"
                "    def test_shared_contract(self):\n"
                "        self.assertEqual(ingest([' a1 ', 'b2']), ['A1', 'B2'])\n"
                "        self.assertEqual(count_orders(['a1', ' A1 ', 'b2']), 2)\n"
            )},
            "a4": {"report.py": (
                "from order_flow import normalize_order_id\n\n"
                "def count_orders(values):\n"
                "    return len({normalize_order_id(value) for value in values})\n"
            )},
        }
        claims = {"a1": a1, "a2": a2, "a3": a3, "a4": a4}
        for name, files in edits.items():
            worktree = root / name
            git(repo, "worktree", "add", "-b", claims[name].work_branch, str(worktree), base)
            for relative, contents in files.items():
                target = worktree / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(contents)
            git(worktree, "add", ".")
            git(worktree, "commit", "-m", claims[name].goal)
            git(worktree, "push", "origin", claims[name].work_branch)
            evidence = verify_local_git(worktree, claims[name].to_mapping())
            controller.complete_claim("demo/order-flow", name, evidence)

        # Ordinary Git integration, followed by the combined functional check.
        for name in edits:
            git(repo, "merge", "--no-ff", "--no-edit", claims[name].work_branch)
            controller.integrate_claim("demo/order-flow", name)
        check = subprocess.run(
            ["python", "-m", "unittest", "discover", "-s", "tests", "-v"],
            cwd=repo, capture_output=True, text=True,
        )
        assert check.returncode == 0, check.stdout + check.stderr

        # Controlled counterfactual: without A3's scope revision, it edits
        # the same function from the original baseline and Git cannot merge.
        vanilla = root / "vanilla-a3"
        git(repo, "worktree", "add", "-b", "vanilla-a3", str(vanilla), base)
        (vanilla / "order_flow.py").write_text(
            "def normalize_order_id(raw: str) -> str:\n"
            "    return raw.strip().lower()\n"
        )
        git(vanilla, "add", ".")
        git(vanilla, "commit", "-m", "uncoordinated API rewrite")
        attempted = subprocess.run(
            ["git", "-C", str(repo), "merge", "--no-ff", "--no-edit", "vanilla-a3"],
            capture_output=True, text=True,
        )
        vanilla_conflict = attempted.returncode != 0 and "order_flow.py" in git(
            repo, "diff", "--name-only", "--diff-filter=U"
        ).splitlines()
        assert vanilla_conflict, attempted.stdout + attempted.stderr
        git(repo, "merge", "--abort")
        rows = []
        for name in edits:
            row = claim_metrics(
                controller.store.list_claim_events("demo/order-flow", name),
                controller.store.list_subgoal_events("demo/order-flow", name),
            )
            row["claim_id"] = name
            row["state"] = controller.store.get("demo/order-flow", name).state.value
            rows.append(row)
        return {
            "case": "four staggered A submissions, shared API and downstream data flow",
            "actors": "scripted A and M; real isolated Git branches and commits",
            "late_overlap_waited_for_m": True,
            "running_a1_revision_unchanged": controller.store.get("demo/order-flow", "a1").revision == a1_revision + 2,
            "a3_claim_revisions": len(controller.store.list_claim_revisions("demo/order-flow", "a3")),
            "git_merge": "clean", "functional_tests": "passed",
            "scripted_vanilla_counterfactual": "Git conflict in order_flow.py",
            "metrics": project_metrics(rows),
        }


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
