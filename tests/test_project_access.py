from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from mcp.server.fastmcp.exceptions import ToolError
from starlette.testclient import TestClient

from tca.app import create_app
from tca.controller import Controller
from tca.mcp_server import create_mcp
from tca.project_access import Principal, ProjectAccess, current_principal
from tca.rules import BasicRuleChecker
from tca.store import ClaimStore


class FakeGitHub:
    def principal(self, token: str) -> Principal:
        if token not in {"owner", "alice", "bob", "reader"}:
            raise PermissionError("invalid token")
        return Principal(token, token)

    def permissions(self, principal: Principal, project_id: str) -> dict:
        if project_id != "org/repo":
            raise PermissionError("not found")
        return {
            "admin": principal.login == "owner",
            "push": principal.login in {"alice", "bob"},
            "pull": True,
        }


def test_owner_starts_project_and_developers_join_by_repo_permission(tmp_path) -> None:
    store = ClaimStore(tmp_path / "projects.db")
    access = ProjectAccess(store, FakeGitHub())
    token = current_principal.set(Principal("alice", "alice"))
    try:
        with pytest.raises(PermissionError, match="not been started"):
            access.join("org/repo")
        with pytest.raises(PermissionError, match="admin"):
            access.start("org/repo")
    finally:
        current_principal.reset(token)

    token = current_principal.set(Principal("owner", "owner"))
    try:
        assert access.start("org/repo")["role"] == "m"
        assert access.join("org/repo")["agent_id"] == "agent-m"
        with pytest.raises(PermissionError, match="A role"):
            access.require("org/repo", "a")
    finally:
        current_principal.reset(token)

    token = current_principal.set(Principal("alice", "alice"))
    try:
        assert access.join("org/repo")["agent_id"] == "agent-alice"
        assert access.list_projects()[0]["role"] == "a"
        with pytest.raises(PermissionError, match="agent-alice"):
            access.require("org/repo", "a", "agent-bob")
        with pytest.raises(PermissionError, match="M role"):
            access.require("org/repo", "m")
    finally:
        current_principal.reset(token)

    token = current_principal.set(Principal("reader", "reader"))
    try:
        assert access.list_projects() == []
        with pytest.raises(PermissionError, match="write access"):
            access.join("org/repo")
    finally:
        current_principal.reset(token)

    class RevokedGitHub(FakeGitHub):
        def permissions(self, principal: Principal, project_id: str) -> dict:
            if principal.login == "alice":
                return {"admin": False, "push": False, "pull": True}
            return super().permissions(principal, project_id)

    access.github = RevokedGitHub()
    token = current_principal.set(Principal("alice", "alice"))
    try:
        with pytest.raises(PermissionError, match="write access"):
            access.join("org/repo")
    finally:
        current_principal.reset(token)


@pytest.mark.anyio
async def test_mcp_enforces_single_developer_identity_and_owner_role(tmp_path) -> None:
    store = ClaimStore(tmp_path / "claims.db")
    access = ProjectAccess(store, FakeGitHub())
    controller = Controller(store, BasicRuleChecker())
    mcp = create_mcp(controller, access)
    token = current_principal.set(Principal("owner", "owner"))
    try:
        access.start("org/repo")
    finally:
        current_principal.reset(token)

    claim = {
        "claim_id": "feature-1", "project_id": "org/repo", "author": "agent-alice",
        "base_commit": "abc123", "work_branch": "feature/agent-alice/feature-1",
        "goal": "Implement feature", "planned_paths": ["src/**"],
    }
    token = current_principal.set(Principal("alice", "alice"))
    try:
        await mcp.call_tool("submit_claim", {"claim": claim})
        with pytest.raises(ToolError, match="one active A Claim"):
            await mcp.call_tool("submit_claim", {"claim": {**claim,
                "claim_id": "feature-2", "work_branch": "feature/agent-alice/feature-2"}})
        with pytest.raises(ToolError, match="M role"):
            await mcp.call_tool("wait_for_coordination_task", {"project_id": "org/repo"})
        with pytest.raises(ToolError, match="agent-alice"):
            await mcp.call_tool("submit_claim", {"claim": {**claim, "claim_id": "feature-2",
                "author": "agent-bob", "work_branch": "feature/agent-bob/feature-2"}})
        await mcp.call_tool("start_execution", {"project_id": "org/repo", "claim_id": "feature-1"})
        await mcp.call_tool("complete_claim", {"project_id": "org/repo", "claim_id": "feature-1"})
        await mcp.call_tool("submit_claim", {"claim": {**claim,
            "claim_id": "feature-2", "work_branch": "feature/agent-alice/feature-2"}})
    finally:
        current_principal.reset(token)

    token = current_principal.set(Principal("bob", "bob"))
    try:
        with pytest.raises(ToolError, match="agent-bob"):
            await mcp.call_tool("start_execution", {"project_id": "org/repo",
                "claim_id": "feature-1"})
    finally:
        current_principal.reset(token)


def test_github_mode_project_api(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("TCA_AUTH_MODE", "github")
    monkeypatch.setenv("TCA_ALLOWED_HOSTS", "testserver")
    monkeypatch.setenv("TCA_DATABASE_PATH", str(tmp_path / "server.db"))
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "webhook-secret")
    monkeypatch.setenv("GITHUB_TOKEN", "test-server-token")
    monkeypatch.delenv("TCA_API_TOKEN", raising=False)
    monkeypatch.setattr("tca.project_access.GitHubAccess.principal", FakeGitHub().principal)
    monkeypatch.setattr("tca.project_access.GitHubAccess.permissions", FakeGitHub().permissions)
    with TestClient(create_app()) as client:
        headers = lambda name: {"Authorization": f"Bearer {name}"}
        assert client.post("/api/projects", json={"project_id": "org/repo"},
                           headers=headers("alice")).status_code == 403
        assert client.post("/api/projects", json={"project_id": "org/repo"},
                           headers=headers("owner")).status_code == 201
        assert client.post("/api/projects/org/repo/join", json={},
                           headers=headers("alice")).json()["agent_id"] == "agent-alice"
        assert client.get("/api/projects", headers=headers("reader")).json() == {"projects": []}
        assert client.get("/api/projects", headers=headers("alice")).json()["projects"][0]["role"] == "a"
        assert client.get("/api/projects", headers=headers("invalid")).status_code == 401

        def signed_push(ref: str):
            body = json.dumps({"ref": ref, "repository": {
                "full_name": "org/repo", "default_branch": "main"}}).encode()
            signature = hmac.new(b"webhook-secret", body, hashlib.sha256).hexdigest()
            return client.post("/webhooks/github", content=body, headers={
                "X-Hub-Signature-256": f"sha256={signature}",
                "X-GitHub-Event": "push",
            })

        assert signed_push("refs/heads/tca/claim/agent-alice/feature-3").status_code == 401
        assert signed_push("refs/heads/main").status_code == 202

        mcp_headers = {
            **headers("alice"), "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": "2025-11-25",
        }
        initialize = client.post("/mcp", headers=mcp_headers, json={
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-11-25", "capabilities": {},
                       "clientInfo": {"name": "test", "version": "1"}},
        })
        assert initialize.status_code == 200, initialize.text
        result = client.post("/mcp", headers=mcp_headers, json={
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "get_claim_state", "arguments": {
                "project_id": "org/repo", "claim_id": "missing"}},
        })
        assert result.status_code == 200, result.text
        assert "GitHub authentication is required" not in result.text
        assert "claim not found" in result.text or "missing" in result.text
