from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass

import yaml

from .controller import Controller
from .domain import Claim, ClaimRecord


def verify_signature(secret: str, body: bytes, signature: str | None) -> bool:
    if not secret or not signature or not signature.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, f"sha256={expected}")


@dataclass(slots=True)
class LocalGitWebhookHandler:
    controller: Controller
    secret: str
    branch_prefix: str = "tca/claim/"

    def handle(
        self,
        body: bytes,
        signature: str | None,
        repository: str | None,
        ref: str | None,
    ) -> ClaimRecord | None:
        if not verify_signature(self.secret, body, signature):
            raise PermissionError("invalid local Git webhook signature")
        if not repository:
            raise ValueError("local Git webhook is missing repository")
        heads_prefix = "refs/heads/"
        if not ref or not ref.startswith(heads_prefix):
            return None
        branch = ref.removeprefix(heads_prefix)
        if not branch.startswith(self.branch_prefix):
            return None
        try:
            value = yaml.safe_load(body)
        except yaml.YAMLError as error:
            raise ValueError("claim file is not valid YAML") from error
        if not isinstance(value, dict):
            raise ValueError("claim file must contain a YAML object")
        claim_id = branch.removeprefix(self.branch_prefix)
        value.setdefault("claim_id", claim_id)
        value.setdefault("project_id", repository)
        if value["claim_id"] != claim_id:
            raise ValueError("claim_id does not match Claim branch")
        if value["project_id"] != repository:
            raise ValueError("project_id does not match local Git repository")
        return self.controller.submit_claim(Claim.from_mapping(value))
