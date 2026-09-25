import hashlib
import hmac
import json

import pytest

from tca.controller import Controller
from tca.domain import Claim, ClaimState, Decision
from tca.github import GitHubClient, GitHubWebhookHandler, parse_claim_branch
from tca.rules import BasicRuleChecker
from tca.store import ClaimStore


class FakeGitHub:
    def __init__(self) -> None:
        self.load_calls: list[tuple[str, str]] = []
        self.commit_calls: list[tuple[str, str]] = []
        self.compare_calls: list[tuple[str, str, str]] = []
        self.ancestors: set[tuple[str, str, str]] = set()
        self.project_id: str | None = None
        self.base_commit = "abc123"
        self.default_head = "abc123"
        self.claim_branch_has_base = True
        self.version = 1

    def load(self, repository: str, branch: str) -> dict[str, object]:
        self.load_calls.append((repository, branch))
        value = {
            "author": "agent-alice",
            "base_commit": self.base_commit,
            "work_branch": "feature/agent-alice/claim-42",
            "goal": "fix login redirect",
            "planned_paths": ["src/login/**"],
            "version": self.version,
        }
        if self.project_id:
            value["project_id"] = self.project_id
        return value

    def get_commit_sha(self, repository: str, ref: str) -> str:
        self.commit_calls.append((repository, ref))
        return self.default_head

    def is_ancestor(
        self, repository: str, ancestor: str, descendant: str
    ) -> bool:
        key = (repository, ancestor, descendant)
        self.compare_calls.append(key)
        if descendant.startswith("tca/claim/"):
            return self.claim_branch_has_base
        return key in self.ancestors


def signed(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def make_handler(
    github: FakeGitHub, controller: Controller | None = None
) -> GitHubWebhookHandler:
    controller = controller or Controller(ClaimStore(), BasicRuleChecker())
    return GitHubWebhookHandler(controller, github, "webhook-secret")


def complete_claim(controller: Controller, claim_id: str, work_branch: str) -> None:
    claim = controller.submit_claim(
        Claim(
            claim_id=claim_id,
            project_id="team/project",
            author=f"agent-{claim_id}",
            base_commit="abc123",
            work_branch=work_branch,
            goal=f"implement {claim_id}",
            planned_paths=(f"src/{claim_id}.py",),
        )
    )
    controller.submit_coordination_decision(
        claim.claim.project_id,
        claim.claim.claim_id,
        Decision(ClaimState.APPROVED, "claim may run"),
    )
    controller.start_execution(claim.claim.project_id, claim.claim.claim_id)
    controller.complete_claim(claim.claim.project_id, claim.claim.claim_id)


def test_github_push_loads_claim_branch_and_submits_to_controller() -> None:
    github = FakeGitHub()
    handler = make_handler(github)
    body = json.dumps(
        {
            "ref": "refs/heads/tca/claim/agent-alice/claim-42",
            "repository": {
                "full_name": "team/project",
                "default_branch": "main",
            },
        }
    ).encode()

    result = handler.handle(body, signed("webhook-secret", body), "push")

    assert result.claim is not None
    assert result.claim.claim.claim_id == "claim-42"
    assert result.claim.claim.project_id == "team/project"
    assert result.claim.state is ClaimState.APPROVED
    assert result.integrated_claims == ()
    assert github.load_calls == [
        ("team/project", "tca/claim/agent-alice/claim-42")
    ]
    assert github.commit_calls == [("team/project", "main")]


def test_unscoped_claim_branch_is_ignored() -> None:
    github = FakeGitHub()
    handler = make_handler(github)
    body = json.dumps(
        {
            "ref": "refs/heads/tca/claim/claim-42",
            "repository": {
                "full_name": "team/project",
                "default_branch": "main",
            },
        }
    ).encode()

    result = handler.handle(body, signed("webhook-secret", body), "push")

    assert not result.accepted
    assert github.load_calls == []


def test_agent_scoped_branch_rejects_author_mismatch() -> None:
    github = FakeGitHub()
    handler = make_handler(github)
    body = json.dumps(
        {
            "ref": "refs/heads/tca/claim/agent-bob/claim-42",
            "repository": {
                "full_name": "team/project",
                "default_branch": "main",
            },
        }
    ).encode()

    with pytest.raises(ValueError, match="author"):
        handler.handle(body, signed("webhook-secret", body), "push")


def test_claim_rejects_project_mismatch() -> None:
    github = FakeGitHub()
    github.project_id = "other/project"
    handler = make_handler(github)
    body = json.dumps(
        {
            "ref": "refs/heads/tca/claim/agent-alice/claim-42",
            "repository": {
                "full_name": "team/project",
                "default_branch": "main",
            },
        }
    ).encode()

    with pytest.raises(ValueError, match="project_id"):
        handler.handle(body, signed("webhook-secret", body), "push")


def test_new_claim_rejects_stale_base_commit() -> None:
    github = FakeGitHub()
    github.default_head = "new-main-head"
    handler = make_handler(github)
    body = json.dumps(
        {
            "ref": "refs/heads/tca/claim/agent-alice/claim-42",
            "repository": {
                "full_name": "team/project",
                "default_branch": "main",
            },
        }
    ).encode()

    with pytest.raises(ValueError, match="current default branch head"):
        handler.handle(body, signed("webhook-secret", body), "push")


def test_claim_rejects_base_outside_claim_branch_history() -> None:
    github = FakeGitHub()
    github.claim_branch_has_base = False
    handler = make_handler(github)
    body = json.dumps(
        {
            "ref": "refs/heads/tca/claim/agent-alice/claim-42",
            "repository": {
                "full_name": "team/project",
                "default_branch": "main",
            },
        }
    ).encode()

    with pytest.raises(ValueError, match="not an ancestor"):
        handler.handle(body, signed("webhook-secret", body), "push")


def test_claim_revision_keeps_original_base_after_default_branch_moves() -> None:
    github = FakeGitHub()
    controller = Controller(ClaimStore(), BasicRuleChecker())
    handler = make_handler(github, controller)
    body = json.dumps(
        {
            "ref": "refs/heads/tca/claim/agent-alice/claim-42",
            "repository": {
                "full_name": "team/project",
                "default_branch": "main",
            },
        }
    ).encode()
    handler.handle(body, signed("webhook-secret", body), "push")

    github.default_head = "new-main-head"
    github.version = 2
    result = handler.handle(body, signed("webhook-secret", body), "push")

    assert result.claim is not None
    assert result.claim.claim.version == 2
    assert result.claim.claim.base_commit == "abc123"
    assert github.commit_calls == [("team/project", "main")]


def test_parse_claim_branch() -> None:
    assert parse_claim_branch("tca/claim/claim-42") is None
    assert parse_claim_branch("tca/claim/agent-a/claim-42") == (
        "agent-a",
        "claim-42",
    )
    assert parse_claim_branch("main") is None
    assert parse_claim_branch("tca/claim/agent-a/claim-42/extra") is None


def test_invalid_signature_is_rejected() -> None:
    handler = make_handler(FakeGitHub())
    body = b"{}"

    with pytest.raises(PermissionError):
        handler.handle(body, "sha256=bad", "push")


def test_default_branch_push_integrates_only_contained_completed_claims() -> None:
    github = FakeGitHub()
    controller = Controller(ClaimStore(), BasicRuleChecker())
    handler = make_handler(github, controller)
    complete_claim(controller, "claim-1", "feature/agent-claim-1/claim-1")
    complete_claim(controller, "claim-2", "feature/agent-claim-2/claim-2")
    github.ancestors.add(
        ("team/project", "feature/agent-claim-1/claim-1", "main")
    )
    body = json.dumps(
        {
            "ref": "refs/heads/main",
            "repository": {
                "full_name": "team/project",
                "default_branch": "main",
            },
        }
    ).encode()

    result = handler.handle(body, signed("webhook-secret", body), "push")

    assert [record.claim.claim_id for record in result.integrated_claims] == [
        "claim-1"
    ]
    assert controller.get_claim_state("team/project", "claim-1")["state"] == (
        "integrated"
    )
    assert controller.get_claim_state("team/project", "claim-2")["state"] == (
        "completed"
    )
    assert github.compare_calls == [
        ("team/project", "feature/agent-claim-1/claim-1", "main"),
        ("team/project", "feature/agent-claim-2/claim-2", "main"),
    ]


def test_default_branch_push_can_integrate_chained_completed_claims() -> None:
    github = FakeGitHub()
    controller = Controller(ClaimStore(), BasicRuleChecker())
    handler = make_handler(github, controller)
    complete_claim(controller, "claim-1", "feature/agent-claim-1/claim-1")
    complete_claim(controller, "claim-2", "feature/agent-claim-2/claim-2")
    github.ancestors.update(
        {
            ("team/project", "feature/agent-claim-1/claim-1", "main"),
            ("team/project", "feature/agent-claim-2/claim-2", "main"),
        }
    )
    body = json.dumps(
        {
            "ref": "refs/heads/main",
            "repository": {
                "full_name": "team/project",
                "default_branch": "main",
            },
        }
    ).encode()

    result = handler.handle(body, signed("webhook-secret", body), "push")

    assert [record.claim.claim_id for record in result.integrated_claims] == [
        "claim-1",
        "claim-2",
    ]


def test_unrelated_branch_is_ignored() -> None:
    github = FakeGitHub()
    handler = make_handler(github)
    body = json.dumps(
        {
            "ref": "refs/heads/feature/example",
            "repository": {
                "full_name": "team/project",
                "default_branch": "main",
            },
        }
    ).encode()

    result = handler.handle(body, signed("webhook-secret", body), "push")

    assert not result.accepted
    assert github.load_calls == []
    assert github.compare_calls == []


@pytest.mark.parametrize(
    ("base_sha", "merge_base_sha", "expected"),
    [("work-tip", "work-tip", True), ("work-tip", "older-base", False)],
)
def test_github_compare_detects_ancestor(
    monkeypatch, base_sha: str, merge_base_sha: str, expected: bool
) -> None:
    urls: list[str] = []

    def fake_get_json(self, url: str) -> dict[str, object]:
        urls.append(url)
        return {
            "base_commit": {"sha": base_sha},
            "merge_base_commit": {"sha": merge_base_sha},
        }

    monkeypatch.setattr(
        GitHubClient,
        "_get_json",
        fake_get_json,
    )

    assert GitHubClient("token").is_ancestor(
        "team/project", "feature/agent-a/claim-1", "main"
    ) is expected
    assert urls == [
        "https://api.github.com/repos/team/project/compare/"
        "feature%2Fagent-a%2Fclaim-1...main"
    ]


def test_github_client_resolves_commit_sha(monkeypatch) -> None:
    urls: list[str] = []

    def fake_get_json(self, url: str) -> dict[str, object]:
        urls.append(url)
        return {"sha": "abc123"}

    monkeypatch.setattr(GitHubClient, "_get_json", fake_get_json)

    assert GitHubClient("token").get_commit_sha("team/project", "main") == "abc123"
    assert urls == ["https://api.github.com/repos/team/project/commits/main"]
