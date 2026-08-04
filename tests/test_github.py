import hashlib
import hmac
import json

import pytest

from tca.controller import Controller
from tca.domain import ClaimState
from tca.github import GitHubWebhookHandler
from tca.rules import BasicRuleChecker
from tca.store import ClaimStore


class FakeLoader:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def load(self, repository: str, branch: str) -> dict[str, object]:
        self.calls.append((repository, branch))
        return {
            "author": "agent-alice",
            "base_commit": "abc123",
            "work_branch": "feature/login-fix",
            "goal": "fix login redirect",
            "planned_paths": ["src/login/**"],
        }


def signed(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def make_handler(loader: FakeLoader) -> GitHubWebhookHandler:
    controller = Controller(ClaimStore(), BasicRuleChecker())
    return GitHubWebhookHandler(controller, loader, "webhook-secret")


def test_github_push_loads_claim_branch_and_submits_to_controller() -> None:
    loader = FakeLoader()
    handler = make_handler(loader)
    body = json.dumps(
        {
            "ref": "refs/heads/tca/claim/claim-42",
            "repository": {"full_name": "team/project"},
        }
    ).encode()

    record = handler.handle(body, signed("webhook-secret", body), "push")

    assert record is not None
    assert record.claim.claim_id == "claim-42"
    assert record.claim.project_id == "team/project"
    assert record.state is ClaimState.ASSESSING
    assert loader.calls == [("team/project", "tca/claim/claim-42")]


def test_invalid_signature_is_rejected() -> None:
    handler = make_handler(FakeLoader())
    body = b"{}"

    with pytest.raises(PermissionError):
        handler.handle(body, "sha256=bad", "push")


def test_unrelated_branch_is_ignored() -> None:
    loader = FakeLoader()
    handler = make_handler(loader)
    body = json.dumps(
        {"ref": "refs/heads/main", "repository": {"full_name": "team/project"}}
    ).encode()

    assert handler.handle(body, signed("webhook-secret", body), "push") is None
    assert loader.calls == []
