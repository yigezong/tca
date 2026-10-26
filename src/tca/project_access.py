"""GitHub-backed project membership for remote TCA sessions."""

from __future__ import annotations

import contextvars
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass


PROJECT_RE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
current_principal: contextvars.ContextVar[Principal | None] = contextvars.ContextVar(
    "tca_principal", default=None
)


@dataclass(frozen=True)
class Principal:
    login: str
    token: str


class GitHubAccess:
    def _get(self, path: str, token: str) -> dict:
        request = urllib.request.Request(
            "https://api.github.com" + path,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "team-code-agent/0.1",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                value = json.load(response)
        except (urllib.error.HTTPError, urllib.error.URLError) as exc:
            raise PermissionError("GitHub identity or repository access could not be verified") from exc
        if not isinstance(value, dict):
            raise PermissionError("invalid GitHub identity response")
        return value

    def principal(self, token: str) -> Principal:
        login = self._get("/user", token).get("login")
        if not isinstance(login, str) or not re.fullmatch(r"[A-Za-z0-9-]+", login):
            raise PermissionError("GitHub token does not identify a user")
        return Principal(login=login, token=token)

    def permissions(self, principal: Principal, project_id: str) -> dict:
        if not PROJECT_RE.fullmatch(project_id):
            raise PermissionError("project must be a GitHub owner/repo")
        repository = self._get(
            "/repos/" + urllib.parse.quote(project_id, safe="/"), principal.token
        )
        if repository.get("full_name", "").lower() != project_id.lower():
            raise PermissionError("GitHub repository identity mismatch")
        permissions = repository.get("permissions")
        if not isinstance(permissions, dict):
            raise PermissionError("GitHub did not return repository permissions")
        return permissions


class ProjectAccess:
    def __init__(self, store, github: GitHubAccess | None = None) -> None:
        self.store = store
        self.github = github or GitHubAccess()

    def principal(self) -> Principal:
        principal = current_principal.get()
        if principal is None:
            raise PermissionError("GitHub authentication is required")
        return principal

    def start(self, project_id: str) -> dict:
        project_id = project_id.lower()
        principal = self.principal()
        if not self.github.permissions(principal, project_id).get("admin"):
            raise PermissionError("repository admin access is required to start a project")
        self.store.create_project(project_id, principal.login)
        return {"project_id": project_id, "owner": self.store.project_owner(project_id),
                "role": "m"}

    def role(self, project_id: str) -> str:
        project_id = project_id.lower()
        principal = self.principal()
        owner = self.store.project_owner(project_id)
        if owner is None:
            raise PermissionError("project has not been started by a repository owner")
        permissions = self.github.permissions(principal, project_id)
        if principal.login.lower() == owner.lower():
            if not permissions.get("admin"):
                raise PermissionError("project owner no longer has repository admin access")
            return "m"
        if permissions.get("push") or permissions.get("maintain") or permissions.get("admin"):
            return "a"
        raise PermissionError("repository write access is required to join")

    def require(self, project_id: str, role: str, author: str | None = None) -> None:
        actual = self.role(project_id)
        if actual != role:
            raise PermissionError(f"this operation requires the project {role.upper()} role")
        if role == "a" and author is not None:
            expected = "agent-" + self.principal().login.lower()
            if author != expected:
                raise PermissionError(f"Claim author must be {expected}")

    def require_claim_owner(self, project_id: str, claim_id: str) -> None:
        self.require(project_id, "a")
        claim = self.store.get(project_id.lower(), claim_id).claim
        self.require(project_id, "a", claim.author)

    def join(self, project_id: str) -> dict:
        project_id = project_id.lower()
        role = self.role(project_id)
        principal = self.principal()
        return {"project_id": project_id, "role": role,
                "agent_id": "agent-m" if role == "m" else "agent-" + principal.login.lower()}

    def list_projects(self) -> list[dict]:
        result = []
        for project_id, owner in self.store.list_projects():
            try:
                role = self.role(project_id)
            except PermissionError:
                continue
            result.append({"project_id": project_id, "owner": owner, "role": role})
        return result
