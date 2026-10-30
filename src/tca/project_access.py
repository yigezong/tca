"""GitHub-backed project membership for remote TCA sessions."""

from __future__ import annotations

import base64
import contextvars
import hashlib
import json
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Protocol

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa, ed25519


PROJECT_RE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
current_principal: contextvars.ContextVar[Principal | None] = contextvars.ContextVar(
    "tca_principal", default=None
)


@dataclass(frozen=True)
class Principal:
    login: str


class RepositoryProvider(Protocol):
    """Host adapter; the controller and Claim protocol do not depend on GitHub."""

    def public_keys(self, login: str) -> list[str]: ...

    def permissions(self, principal: Principal, project_id: str) -> dict: ...


class GitHubAccess:
    def __init__(self, app_id: str = "", private_key: str = "") -> None:
        self.app_id = app_id or os.getenv("TCA_GITHUB_APP_ID", "")
        self.private_key = private_key or os.getenv("TCA_GITHUB_APP_PRIVATE_KEY", "").replace("\\n", "\n")
        self._installation_tokens: dict[str, tuple[str, float]] = {}
        self._public_key_token: tuple[str, float] | None = None
        self._lock = threading.RLock()

    def _request(self, path: str, token: str = "", body: dict | None = None,
                 *, expect_list: bool = False) -> dict | list:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "team-code-agent/0.1",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            "https://api.github.com" + path,
            headers=headers,
            data=json.dumps(body).encode() if body is not None else None,
            method="POST" if body is not None else "GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                value = json.load(response)
        except (urllib.error.HTTPError, urllib.error.URLError) as exc:
            raise PermissionError("GitHub identity or repository access could not be verified") from exc
        if not isinstance(value, list if expect_list else dict):
            raise PermissionError("invalid GitHub identity response")
        return value

    def public_keys(self, login: str) -> list[str]:
        if not re.fullmatch(r"[A-Za-z0-9-]+", login):
            raise PermissionError("invalid GitHub login")
        # Anonymous GitHub API calls share a small IP-wide rate limit. Authenticate
        # even this public lookup so one busy host cannot prevent every login.
        with self._lock:
            cached = self._public_key_token
            if cached and cached[1] > time.time() + 60:
                token = cached[0]
            else:
                jwt = self._app_jwt()
                installations = self._request("/app/installations?per_page=1", jwt,
                                              expect_list=True)
                installation_id = installations[0].get("id") if installations else None
                if not isinstance(installation_id, int):
                    raise PermissionError("GitHub App has no active installation")
                issued = self._request(
                    f"/app/installations/{installation_id}/access_tokens", jwt,
                    {"permissions": {"metadata": "read"}},
                )
                token, expires = issued.get("token"), issued.get("expires_at")
                if not isinstance(token, str) or not isinstance(expires, str):
                    raise PermissionError("GitHub App installation token unavailable")
                from datetime import datetime
                self._public_key_token = (
                    token, datetime.fromisoformat(expires.replace("Z", "+00:00")).timestamp()
                )
        keys = self._request("/users/" + urllib.parse.quote(login) + "/keys",
                             token, expect_list=True)
        if not isinstance(keys, list):
            raise PermissionError("invalid GitHub public key response")
        return [item["key"] for item in keys if isinstance(item, dict) and isinstance(item.get("key"), str)]

    def _app_jwt(self) -> str:
        if not self.app_id or not self.private_key:
            raise PermissionError("GitHub App is not configured")
        now = int(time.time())
        header = base64.urlsafe_b64encode(b'{"alg":"RS256","typ":"JWT"}').rstrip(b"=")
        payload = base64.urlsafe_b64encode(json.dumps({
            "iat": now - 60, "exp": now + 540, "iss": self.app_id,
        }, separators=(",", ":")).encode()).rstrip(b"=")
        message = header + b"." + payload
        key = serialization.load_pem_private_key(self.private_key.encode(), password=None)
        if not isinstance(key, rsa.RSAPrivateKey):
            raise PermissionError("GitHub App key must be RSA")
        signature = key.sign(message, padding.PKCS1v15(), hashes.SHA256())
        return (message + b"." + base64.urlsafe_b64encode(signature).rstrip(b"=")).decode()

    def permissions(self, principal: Principal, project_id: str) -> dict:
        if not PROJECT_RE.fullmatch(project_id):
            raise PermissionError("project must be a GitHub owner/repo")
        repo = urllib.parse.quote(project_id, safe="/")
        with self._lock:
            cached = self._installation_tokens.get(project_id.lower())
            if cached and cached[1] > time.time() + 60:
                token = cached[0]
            else:
                jwt = self._app_jwt()
                installation = self._request(f"/repos/{repo}/installation", jwt)
                installation_id = installation.get("id")
                if not isinstance(installation_id, int):
                    raise PermissionError("GitHub App is not installed on this repository")
                issued = self._request(
                    f"/app/installations/{installation_id}/access_tokens", jwt,
                    {"repositories": [project_id.split("/", 1)[1]],
                     "permissions": {"metadata": "read"}},
                )
                token = issued.get("token")
                expires = issued.get("expires_at")
                if not isinstance(token, str) or not isinstance(expires, str):
                    raise PermissionError("GitHub App installation token unavailable")
                from datetime import datetime
                self._installation_tokens[project_id.lower()] = (
                    token, datetime.fromisoformat(expires.replace("Z", "+00:00")).timestamp()
                )
        path = f"/repos/{repo}/collaborators/{urllib.parse.quote(principal.login)}/permission"
        response = self._request(path, token)
        permission = response.get("permission", "none")
        role = response.get("role_name", permission)
        if permission not in {"admin", "write", "read", "none"}:
            raise PermissionError("invalid GitHub repository permission response")
        return {"admin": permission == "admin", "push": permission in {"admin", "write"},
                "maintain": role == "maintain", "pull": permission != "none"}


class TcaIdentity:
    """A GitHub-listed SSH public key signs login; only TCA-scoped sessions cross MCP."""

    def __init__(self, store, github: RepositoryProvider) -> None:
        self.store = store
        self.github = github

    def challenge(self, login: str) -> dict:
        if not re.fullmatch(r"[A-Za-z0-9-]+", login):
            raise PermissionError("invalid GitHub login")
        return self.store.create_auth_challenge(login.lower())

    def login(self, login: str, challenge_id: str, public_key: str, signature: str) -> dict:
        login = login.lower()
        challenge = self.store.consume_auth_challenge(challenge_id, login)
        if not challenge:
            raise PermissionError("invalid or expired login challenge")
        try:
            kind, encoded, *_ = public_key.strip().split()
            canonical = f"{kind} {encoded}"
            if canonical not in {" ".join(key.split()[:2]) for key in self.github.public_keys(login)}:
                raise PermissionError("SSH public key is not registered on GitHub")
            key = serialization.load_ssh_public_key(canonical.encode())
            if not isinstance(key, ed25519.Ed25519PublicKey):
                raise PermissionError("TCA login requires an Ed25519 SSH key")
            signed = f"tca-login-v1\n{challenge_id}\n{challenge}".encode()
            key.verify(base64.b64decode(signature, validate=True), signed)
        except PermissionError:
            raise
        except Exception as exc:
            raise PermissionError("invalid TCA login signature") from exc
        token = "tca_" + secrets.token_urlsafe(32)
        expires_at = self.store.create_auth_session(hashlib.sha256(token.encode()).hexdigest(), login)
        return {"token": token, "expires_at": expires_at, "login": login}

    def principal(self, token: str) -> Principal:
        if not token.startswith("tca_"):
            raise PermissionError("a TCA session token is required")
        login = self.store.auth_session_login(hashlib.sha256(token.encode()).hexdigest())
        if not login:
            raise PermissionError("invalid or expired TCA session")
        return Principal(login)


class ProjectAccess:
    def __init__(self, store, github: RepositoryProvider | None = None) -> None:
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
        role = self.role(project_id)
        return {"project_id": project_id, "owner": self.store.project_owner(project_id),
                "manager": self.store.project_manager(project_id), "role": role,
                "agent_id": "agent-m" if role == "m" else "agent-" + principal.login.lower()}

    def delegate_manager(self, project_id: str, login: str) -> dict:
        project_id, login = project_id.lower(), login.lower()
        principal = self.principal()
        owner = self.store.project_owner(project_id)
        if owner is None:
            raise PermissionError("project has not been started")
        if principal.login.lower() != owner.lower():
            raise PermissionError("only the project creator can delegate M")
        if not self.github.permissions(principal, project_id).get("admin"):
            raise PermissionError("project creator must retain repository admin access")
        if not re.fullmatch(r"[a-z0-9-]+", login):
            raise PermissionError("invalid manager login")
        if not self.github.permissions(Principal(login), project_id).get("pull"):
            raise PermissionError("manager must have repository read access")
        self.store.set_project_manager(project_id, login)
        return {"project_id": project_id, "owner": principal.login,
                "manager": login}

    def require_project_admin(self, project_id: str) -> None:
        owner = self.store.project_owner(project_id.lower())
        principal = self.principal()
        if owner is None or principal.login.lower() != owner.lower():
            raise PermissionError("project creator is required")
        if not self.github.permissions(principal, project_id.lower()).get("admin"):
            raise PermissionError("repository admin access is required")

    def role(self, project_id: str) -> str:
        project_id = project_id.lower()
        principal = self.principal()
        owner = self.store.project_owner(project_id)
        if owner is None:
            raise PermissionError("project has not been started by a repository owner")
        permissions = self.github.permissions(principal, project_id)
        if principal.login.lower() == (self.store.project_manager(project_id) or owner).lower():
            if not permissions.get("pull"):
                raise PermissionError("project manager no longer has repository access")
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
                "agent_id": "agent-m" if role == "m" else "agent-" + principal.login.lower(),
                "manager": self.store.project_manager(project_id)}

    def list_projects(self) -> list[dict]:
        result = []
        for project_id, owner in self.store.list_projects():
            try:
                role = self.role(project_id)
            except PermissionError:
                continue
            result.append({"project_id": project_id, "owner": owner, "role": role})
        return result
