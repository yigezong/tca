import hashlib
import hmac

import pytest

from tca.controller import Controller
from tca.domain import ClaimState
from tca.local_git import LocalGitWebhookHandler
from tca.rules import BasicRuleChecker
from tca.store import ClaimStore


def signed(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def claim_yaml(claim_id: str = "claim-42") -> bytes:
    return f"""claim_id: {claim_id}
project_id: team/project
author: agent-a1
base_commit: abc123
work_branch: feature/{claim_id}
goal: test local Git webhook
planned_paths:
  - src/local/**
version: 1
""".encode()


def make_handler(*, external: bool = False) -> tuple[Controller, LocalGitWebhookHandler]:
    controller = Controller(
        ClaimStore(),
        BasicRuleChecker(),
        require_external_coordination=external,
    )
    return controller, LocalGitWebhookHandler(controller, "hook-secret")


def test_local_git_claim_is_queued_for_external_m() -> None:
    controller, handler = make_handler(external=True)
    body = claim_yaml()

    record = handler.handle(
        body,
        signed("hook-secret", body),
        "team/project",
        "refs/heads/tca/claim/claim-42",
    )

    assert record is not None
    assert record.state is ClaimState.ASSESSING
    task = controller.get_coordination_task("team/project")
    assert task is not None
    assert task.claim.claim.claim_id == "claim-42"


def test_local_git_rejects_invalid_signature() -> None:
    _, handler = make_handler()

    with pytest.raises(PermissionError):
        handler.handle(
            claim_yaml(), "sha256=bad", "team/project", "refs/heads/tca/claim/claim-42"
        )


def test_local_git_rejects_branch_claim_mismatch() -> None:
    _, handler = make_handler()
    body = claim_yaml("different-id")

    with pytest.raises(ValueError, match="does not match"):
        handler.handle(
            body,
            signed("hook-secret", body),
            "team/project",
            "refs/heads/tca/claim/claim-42",
        )


def test_local_git_ignores_non_claim_branch() -> None:
    _, handler = make_handler()
    body = claim_yaml()

    assert handler.handle(
        body, signed("hook-secret", body), "team/project", "refs/heads/main"
    ) is None
