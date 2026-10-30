"""Exercise hosted protocol boundaries with separate identities over HTTP."""

from __future__ import annotations

import base64
import json
import sqlite3

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from starlette.testclient import TestClient

from tca.app import create_app
from tca.project_access import GitHubAccess


class Repository:
    def __init__(self, keys: dict[str, str]) -> None:
        self.keys = keys
        self.revoked: set[str] = set()

    def public_keys(self, login: str) -> list[str]:
        return [self.keys[login]] if login in self.keys else []

    def permissions(self, principal, project_id: str) -> dict:
        if project_id != "org/repo" or principal.login in self.revoked:
            raise PermissionError("repository access revoked")
        return {
            "admin": principal.login == "owner",
            "push": principal.login in {"a1", "a2"},
            "pull": principal.login in {"owner", "manager", "a1", "a2"},
        }


def test_public_keys_use_app_authentication(monkeypatch) -> None:
    """A shared host can exhaust GitHub's anonymous IP-wide API limit."""
    access = GitHubAccess("app-id", "unused")
    monkeypatch.setattr(access, "_app_jwt", lambda: "app-jwt")
    calls = []

    def request(path, token="", body=None, *, expect_list=False):
        calls.append((path, token, body, expect_list))
        if path == "/app/installations?per_page=1":
            return [{"id": 123}]
        if path == "/app/installations/123/access_tokens":
            return {"token": "installation-token", "expires_at": "2099-01-01T00:00:00Z"}
        if path == "/users/owner/keys":
            return [{"key": "ssh-ed25519 key-material"}]
        raise AssertionError(path)

    monkeypatch.setattr(access, "_request", request)
    assert access.public_keys("owner") == ["ssh-ed25519 key-material"]
    assert access.public_keys("owner") == ["ssh-ed25519 key-material"]
    assert calls[0] == ("/app/installations?per_page=1", "app-jwt", None, True)
    assert calls[1] == ("/app/installations/123/access_tokens", "app-jwt",
                        {"permissions": {"metadata": "read"}}, False)
    assert all(token == "installation-token" for path, token, _, _ in calls
               if path == "/users/owner/keys")
    assert len([path for path, _, _, _ in calls if path.endswith("/access_tokens")]) == 1


def test_separate_people_and_tokens_over_hosted_http(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("TCA_AUTH_MODE", "github")
    monkeypatch.setenv("TCA_ALLOWED_HOSTS", "testserver")
    monkeypatch.setenv("TCA_DATABASE_PATH", str(tmp_path / "hosted.db"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_WEBHOOK_SECRET", raising=False)
    monkeypatch.delenv("TCA_API_TOKEN", raising=False)

    people = {name: ed25519.Ed25519PrivateKey.generate()
              for name in ("owner", "manager", "a1", "a2", "outsider")}
    keys = {name: key.public_key().public_bytes(
        serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH
    ).decode() for name, key in people.items()}
    repository = Repository(keys)

    with TestClient(create_app(github_override=repository)) as client:
        def challenge(login: str) -> dict:
            response = client.post("/api/auth/challenge", json={"login": login})
            assert response.status_code == 200, response.text
            return response.json()

        def login(name: str) -> str:
            issued = challenge(name)
            signed = f"tca-login-v1\n{issued['challenge_id']}\n{issued['nonce']}".encode()
            response = client.post("/api/auth/login", json={
                "login": name,
                "challenge_id": issued["challenge_id"],
                "public_key": keys[name],
                "signature": base64.b64encode(people[name].sign(signed)).decode(),
            })
            assert response.status_code == 200, response.text
            return response.json()["token"]

        def headers(token: str) -> dict[str, str]:
            return {"Authorization": f"Bearer {token}"}

        def tool(token: str, name: str, arguments: dict, *, error: bool = False) -> dict:
            response = client.post("/mcp", headers={
                **headers(token), "Accept": "application/json, text/event-stream",
                "MCP-Protocol-Version": "2025-11-25",
            }, json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                     "params": {"name": name, "arguments": arguments}})
            assert response.status_code == 200, response.text
            result = response.json()["result"]
            assert bool(result.get("isError")) is error, result
            if error:
                return result
            structured = result.get("structuredContent")
            if isinstance(structured, dict):
                return structured.get("result", structured)
            return json.loads(result["content"][0]["text"])

        # A valid public key for another account cannot impersonate the owner.
        stolen = challenge("owner")
        signed = f"tca-login-v1\n{stolen['challenge_id']}\n{stolen['nonce']}".encode()
        forged = client.post("/api/auth/login", json={
            "login": "owner", "challenge_id": stolen["challenge_id"],
            "public_key": keys["a1"],
            "signature": base64.b64encode(people["a1"].sign(signed)).decode(),
        })
        assert forged.status_code == 401
        assert client.post("/api/auth/login", json={
            "login": "owner", "challenge_id": stolen["challenge_id"],
            "public_key": keys["owner"],
            "signature": base64.b64encode(people["owner"].sign(signed)).decode(),
        }).status_code == 401  # challenge was consumed, even on failed login

        sessions = {name: login(name) for name in people}
        assert len(set(sessions.values())) == len(people)
        assert client.get("/api/projects", headers=headers("gho_personal_token")).status_code == 401
        assert client.get("/api/projects", headers=headers("tca_invalid")).status_code == 401
        assert client.get("/api/projects", headers=headers(sessions["owner"])).status_code == 200

        assert client.post("/api/projects", json={"project_id": "org/repo"},
                           headers=headers(sessions["a1"])).status_code == 403
        started = client.post("/api/projects", json={"project_id": "org/repo"},
                              headers=headers(sessions["owner"]))
        assert started.status_code == 201, started.text
        assert client.post("/api/projects/org/repo/manager", json={"login": "manager"},
                           headers=headers(sessions["owner"])).status_code == 200
        assert client.post("/api/projects/org/repo/join", json={},
                           headers=headers(sessions["manager"])).json()["agent_id"] == "agent-m"
        assert client.post("/api/projects/org/repo/join", json={},
                           headers=headers(sessions["a2"])).json()["agent_id"] == "agent-a2"
        assert client.post("/api/projects/org/repo/join", json={},
                           headers=headers(sessions["outsider"])).status_code == 403

        first = {"claim_id": "first", "project_id": "org/repo", "author": "agent-a1",
                 "base_commit": "abc123", "work_branch": "feature/agent-a1/first",
                 "goal": "A1 works on an interface", "planned_paths": ["src/shared.py"],
                 "coordination_group": "pair-1", "expected_group_size": 1}
        second = {"claim_id": "second", "project_id": "org/repo", "author": "agent-a2",
                  "base_commit": "abc123", "work_branch": "feature/agent-a2/second",
                  "goal": "A2 adds a dependent feature", "planned_paths": ["src/shared.py"],
                  "coordination_group": "pair-1", "expected_group_size": 2}
        assert tool(sessions["a1"], "submit_claim", {"claim": first})["state"] == "approved"
        first_task = tool(sessions["manager"], "wait_for_coordination_task", {
            "project_id": "org/repo", "timeout_seconds": 0})
        assert first_task["claim"]["claim"]["claim_id"] == "first"
        tool(sessions["manager"], "submit_coordination_decision", {
            "project_id": "org/repo", "claim_id": "first", "state": "approved",
            "reason": "A1 may start", "worker_id": "agent-m",
        })
        assert tool(sessions["a1"], "start_execution", {
            "project_id": "org/repo", "claim_id": "first"})["state"] == "executing"
        tool(sessions["a1"], "submit_claim", {"claim": second}, error=True)
        assert tool(sessions["a2"], "submit_claim", {"claim": second})["state"] == "approved"
        assert client.get("/api/claims/first?project_id=org/repo",
                          headers=headers(sessions["a1"])).json()["state"] == "executing"
        assert tool(sessions["a2"], "wait_for_coordination_task", {
            "project_id": "org/repo", "timeout_seconds": 0}, error=True)
        task = tool(sessions["manager"], "wait_for_coordination_task", {
            "project_id": "org/repo", "timeout_seconds": 0})
        assert task["claim"]["claim"]["claim_id"] == "second"
        tool(sessions["manager"], "publish_coordination_advice", {"advice": {
            "project_id": "org/repo", "claim_id": "second", "advisor": "agent-m",
            "message": "Preserve A1's interface while integrating your change.",
        }})
        a2_context = tool(sessions["a2"], "get_integration_context", {
            "project_id": "org/repo", "claim_id": "second"})
        a1_context = tool(sessions["a1"], "get_integration_context", {
            "project_id": "org/repo", "claim_id": "first"})
        assert len(a2_context["advice"]) == 1
        assert a1_context["advice"] == []

        path = "/api/projects/org/repo/claims/second/revisions"
        assert len(client.get(path, headers=headers(sessions["manager"])).json()["revisions"]) == 1
        assert client.get(path, headers=headers(sessions["outsider"])).status_code == 403
        with sqlite3.connect(tmp_path / "hosted.db") as db:
            dump = "\n".join(db.iterdump())
        assert all(token not in dump for token in sessions.values())

        repository.revoked.add("a2")
        assert client.post("/api/projects/org/repo/join", json={},
                           headers=headers(sessions["a2"])).status_code == 403
        assert client.get(path, headers=headers(sessions["a2"])).status_code == 403

        with sqlite3.connect(tmp_path / "hosted.db") as db:
            db.execute("UPDATE auth_sessions SET expires_at = '2000-01-01T00:00:00+00:00' "
                       "WHERE login = 'a1'")
        assert client.get("/api/projects", headers=headers(sessions["a1"])).status_code == 401
