from __future__ import annotations

import base64
import hashlib
import hmac
import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol

import yaml

from .controller import Controller
from .domain import Claim, ClaimRecord

CLAIM_BRANCH_PREFIX = "tca/claim/"
CLAIM_FILE_PATH = ".tca/claim.yaml"
GITHUB_CONTENTS_API_URL = "https://api.github.com"


class ClaimLoader(Protocol):
    def load(self, repository: str, branch: str) -> dict[str, Any]: ...


@dataclass(slots=True)
class GitHubContentLoader:
    token: str

    def load(self, repository: str, branch: str) -> dict[str, Any]:
        encoded_repo = urllib.parse.quote(repository, safe="/")
        encoded_path = urllib.parse.quote(CLAIM_FILE_PATH, safe="/")
        encoded_branch = urllib.parse.quote(branch, safe="")
        url = (
            f"{GITHUB_CONTENTS_API_URL}/repos/{encoded_repo}/contents/{encoded_path}"
            f"?ref={encoded_branch}"
        )
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "team-code-agent/0.1",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.load(response)
        if payload.get("encoding") != "base64":
            raise ValueError("GitHub claim content is not base64 encoded")
        raw = base64.b64decode(payload["content"]).decode("utf-8")
        value = yaml.safe_load(raw)
        if not isinstance(value, dict):
            raise ValueError("claim file must contain a YAML object")
        return value


def verify_github_signature(secret: str, body: bytes, signature: str | None) -> bool:
    if not secret or not signature or not signature.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, f"sha256={expected}")


def parse_claim_branch(branch: str) -> tuple[str | None, str] | None:
    if not branch.startswith(CLAIM_BRANCH_PREFIX):
        return None
    suffix = branch.removeprefix(CLAIM_BRANCH_PREFIX).strip("/")
    parts = [part for part in suffix.split("/") if part]
    if len(parts) == 1:
        return None, parts[0]
    if len(parts) == 2:
        return parts[0], parts[1]
    return None


@dataclass(slots=True)
class GitHubWebhookHandler:
    controller: Controller
    loader: ClaimLoader
    secret: str

    def handle(
        self, body: bytes, signature: str | None, event: str | None
    ) -> ClaimRecord | None:
        if not verify_github_signature(self.secret, body, signature):
            raise PermissionError("invalid GitHub webhook signature")
        if event != "push":
            return None
        payload = json.loads(body)
        ref = str(payload.get("ref", ""))
        heads_prefix = "refs/heads/"
        if not ref.startswith(heads_prefix):
            return None
        branch = ref.removeprefix(heads_prefix)
        claim_branch = parse_claim_branch(branch)
        if claim_branch is None:
            return None
        branch_author, claim_id = claim_branch
        repository = str(payload.get("repository", {}).get("full_name", ""))
        if not repository:
            raise ValueError("GitHub webhook is missing repository.full_name")
        value = self.loader.load(repository, branch)
        value.setdefault("claim_id", claim_id)
        value.setdefault("project_id", repository)
        if value["claim_id"] != claim_id:
            raise ValueError("claim_id does not match Claim branch")
        if branch_author is not None and value.get("author") != branch_author:
            raise ValueError("author does not match Claim branch")
        return self.controller.submit_claim(Claim.from_mapping(value))
