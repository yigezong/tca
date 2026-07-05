from __future__ import annotations

import base64
import hashlib
import hmac
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol

import yaml

from .controller import Controller
from .domain import Claim, ClaimRecord


class ClaimLoader(Protocol):
    def load(self, repository: str, branch: str) -> dict[str, Any]: ...


class DecisionPublisher(Protocol):
    def publish(self, repository: str, branch: str, record: ClaimRecord) -> None: ...


@dataclass(slots=True)
class GitHubContentLoader:
    token: str
    claim_path: str = ".tca/claim.yaml"
    api_url: str = "https://api.github.com"

    def load(self, repository: str, branch: str) -> dict[str, Any]:
        encoded_repo = urllib.parse.quote(repository, safe="/")
        encoded_path = urllib.parse.quote(self.claim_path, safe="/")
        encoded_branch = urllib.parse.quote(branch, safe="")
        url = (
            f"{self.api_url}/repos/{encoded_repo}/contents/{encoded_path}"
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



@dataclass(slots=True)
class GitHubDecisionPublisher:
    token: str
    decision_path: str = ".tca/decision.yaml"
    api_url: str = "https://api.github.com"

    def publish(self, repository: str, branch: str, record: ClaimRecord) -> None:
        if not self.token:
            raise ValueError("GITHUB_TOKEN is required to publish decisions")
        url = self._content_url(repository)
        existing_sha = self._existing_sha(url, branch)
        content = yaml.safe_dump(
            {
                "claim_id": record.claim.claim_id,
                "claim_version": record.claim.version,
                "state": record.state.value,
                "revision": record.revision,
                "decision": record.decision.to_mapping() if record.decision else None,
                "updated_at": record.updated_at,
            },
            allow_unicode=True,
            sort_keys=False,
        ).encode("utf-8")
        payload: dict[str, Any] = {
            "message": f"tca: record decision for {record.claim.claim_id}",
            "content": base64.b64encode(content).decode("ascii"),
            "branch": branch,
        }
        if existing_sha:
            payload["sha"] = existing_sha
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            method="PUT",
            headers=self._headers(),
        )
        with urllib.request.urlopen(request, timeout=15):
            pass

    def _existing_sha(self, url: str, branch: str) -> str | None:
        request = urllib.request.Request(
            f"{url}?ref={urllib.parse.quote(branch, safe='')}",
            headers=self._headers(),
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                return str(json.load(response)["sha"])
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return None
            raise

    def _content_url(self, repository: str) -> str:
        encoded_repo = urllib.parse.quote(repository, safe="/")
        encoded_path = urllib.parse.quote(self.decision_path, safe="/")
        return f"{self.api_url}/repos/{encoded_repo}/contents/{encoded_path}"

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "User-Agent": "team-code-agent/0.1",
            "X-GitHub-Api-Version": "2022-11-28",
        }

def verify_github_signature(secret: str, body: bytes, signature: str | None) -> bool:
    if not secret or not signature or not signature.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, f"sha256={expected}")


@dataclass(slots=True)
class GitHubWebhookHandler:
    controller: Controller
    loader: ClaimLoader
    secret: str
    publisher: DecisionPublisher | None = None
    branch_prefix: str = "tca/claim/"

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
        if not branch.startswith(self.branch_prefix):
            return None
        repository = str(payload.get("repository", {}).get("full_name", ""))
        if not repository:
            raise ValueError("GitHub webhook is missing repository.full_name")
        value = self.loader.load(repository, branch)
        claim_id = branch.removeprefix(self.branch_prefix)
        value.setdefault("claim_id", claim_id)
        value.setdefault("project_id", repository)
        if value["claim_id"] != claim_id:
            raise ValueError("claim_id does not match Claim branch")
        record = self.controller.submit_claim(Claim.from_mapping(value))
        if self.publisher is not None:
            self.publisher.publish(repository, branch, record)
        return record
