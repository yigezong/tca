from __future__ import annotations

import argparse
import base64
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from tca.codex_setup import (
    AGENT_NOTE,
    SetupError,
    desired_transport,
    project_id_for,
    project_command,
    login_command,
    _session_path,
    setup,
    transport_matches,
)


def make_repo(path: Path) -> Path:
    path.mkdir()
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    (path / "AGENTS.md").write_text("Existing project rules.\n")
    return path


def args(repo: Path, **changes: object) -> argparse.Namespace:
    defaults = dict(repo=repo, agent_id="agent-a1", project_id=None,
                    mode="local", server_url="", token_env="TCA_API_TOKEN",
                    replace_mcp=False)
    defaults.update(changes)
    return argparse.Namespace(**defaults)


def test_local_init_uses_real_codex_cli_without_touching_user_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path / "project")
    (tmp_path / "codex-home").mkdir()
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    setup(args(repo))
    setup(args(repo))  # no duplicate instructions or MCP entry

    assert (repo / ".tca/project.yaml").read_text().find("mode: local") >= 0
    assert (repo / ".tca/agent-id").read_text() == "agent-a1\n"
    assert (repo / "AGENTS.md").read_text().count(AGENT_NOTE) == 1
    assert ".tca/agent-id" in (repo / ".gitignore").read_text()
    assert (repo / ".agents/skills/tca-coordination/SKILL.md").exists()
    config = subprocess.run(["codex", "mcp", "get", "tca_local", "--json"],
                            text=True, capture_output=True, check=True)
    assert '"type": "stdio"' in config.stdout
    assert "tca.local_mcp" in config.stdout


def test_setup_preserves_existing_files_on_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(tmp_path / "project")
    (tmp_path / "codex-home").mkdir()
    (repo / ".tca").mkdir()
    (repo / ".tca/project.yaml").write_text("existing: true\n")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    with pytest.raises(SetupError, match="Refusing to overwrite"):
        setup(args(repo))
    assert (repo / ".tca/project.yaml").read_text() == "existing: true\n"
    assert not (repo / ".agents").exists()


def test_second_worktree_keeps_project_identity_without_remote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "codex-home").mkdir()
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    first = make_repo(tmp_path / "first")
    setup(args(first))
    second = make_repo(tmp_path / "manager-worktree")
    shutil.copytree(first / ".tca", second / ".tca", ignore=shutil.ignore_patterns("agent-id"))
    shutil.copytree(first / ".agents", second / ".agents")
    shutil.copy(first / "AGENTS.md", second / "AGENTS.md")
    setup(args(second, agent_id="agent-m"))
    assert "project_id: local/first" in (second / ".tca/project.yaml").read_text()
    assert (second / ".tca/agent-id").read_text() == "agent-m\n"


def test_existing_codex_mcp_requires_explicit_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "codex-home").mkdir()
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    repo = make_repo(tmp_path / "project")
    subprocess.run(["codex", "mcp", "add", "tca", "--url", "https://team.example/mcp"],
                   check=True, capture_output=True)
    subprocess.run(["codex", "mcp", "add", "tca_local", "--url", "https://old.example/mcp"],
                   check=True, capture_output=True)
    with pytest.raises(SetupError, match="replace-mcp"):
        setup(args(repo))
    old = subprocess.run(["codex", "mcp", "get", "tca_local", "--json"],
                         check=True, capture_output=True, text=True)
    assert "https://old.example/mcp" in old.stdout
    setup(args(repo, replace_mcp=True))
    new = subprocess.run(["codex", "mcp", "get", "tca_local", "--json"],
                         check=True, capture_output=True, text=True)
    assert "tca.local_mcp" in new.stdout
    remote = subprocess.run(["codex", "mcp", "get", "tca", "--json"],
                            check=True, capture_output=True, text=True)
    assert "https://team.example/mcp" in remote.stdout


def test_remote_transport_requires_https() -> None:
    with pytest.raises(SetupError, match="https"):
        desired_transport("remote", "http://example.com/mcp", "TCA_API_TOKEN")


def test_explicit_local_to_remote_migration(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "codex-home").mkdir()
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    repo = make_repo(tmp_path / "project")
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin",
                    "git@github.com:owner/repo.git"], check=True)
    setup(args(repo))
    remote = args(repo, agent_id="agent-m", mode="remote",
                  server_url="https://tca.example.com/mcp",
                  token_env="TCA_GITHUB_TOKEN")
    with pytest.raises(SetupError, match="Refusing to overwrite"):
        setup(remote)
    remote.replace_local_config = True
    setup(remote)
    assert "mode: remote" in (repo / ".tca/project.yaml").read_text()
    assert (repo / ".tca/agent-id").read_text() == "agent-m\n"


def test_existing_remote_config_can_migrate_off_github_token(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "codex-home").mkdir()
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    repo = make_repo(tmp_path / "project")
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin",
                    "git@github.com:owner/repo.git"], check=True)
    old = args(repo, agent_id="agent-m", mode="remote",
               server_url="https://tca.example.com/mcp", token_env="TCA_GITHUB_TOKEN")
    setup(old)
    skill = repo / ".agents/skills/tca-coordination/SKILL.md"
    skill.write_text("---\nname: tca-coordination\n---\n# TCA in Codex\nold workflow\n")
    new = args(repo, agent_id="agent-m", mode="remote",
               server_url="https://tca.example.com/mcp", token_env="TCA_TOKEN")
    with pytest.raises(SetupError, match="Refusing to overwrite"):
        setup(new)
    new.replace_local_config = True
    new.replace_mcp = True
    setup(new)
    assert "token_env: TCA_TOKEN" in (repo / ".tca/project.yaml").read_text()
    assert "old workflow" not in skill.read_text()


def test_cli_login_keeps_key_local_and_writes_tca_only_session(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    server_url = "https://tca.example.com/mcp"
    calls = []

    def fake_api(url, token, path, body=None):
        calls.append((url, token, path, body))
        if path == "/api/auth/challenge":
            return {"challenge_id": "one-time", "nonce": "random"}
        assert path == "/api/auth/login"
        assert token == ""
        from cryptography.hazmat.primitives import serialization
        public_key = serialization.load_ssh_public_key(body["public_key"].encode())
        public_key.verify(base64.b64decode(body["signature"]),
                          b"tca-login-v1\none-time\nrandom")
        return {"token": "tca_scoped-session", "login": "alice",
                "expires_at": "2099-01-01T00:00:00Z"}

    monkeypatch.setattr("tca.codex_setup._project_api", fake_api)
    login_command(argparse.Namespace(server_url=server_url, login="alice",
                                     key_path=tmp_path / ".tca" / "identity"))
    assert len(calls) == 2
    assert (tmp_path / ".tca" / "identity").stat().st_mode & 0o777 == 0o600
    path = _session_path(server_url)
    assert path.stat().st_mode & 0o777 == 0o600
    assert json.loads(path.read_text())["token"] == "tca_scoped-session"


def test_transport_match_checks_all_requested_fields() -> None:
    desired = {"type": "stdio", "command": "/python", "args": ["-m", "tca.local_mcp"]}
    assert transport_matches({**desired, "env": None}, desired)
    assert not transport_matches({**desired, "args": []}, desired)


def test_project_id_from_github_remote(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "project")
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin",
                    "git@github.com:owner/repo.git"], check=True)
    assert project_id_for(repo) == "owner/repo"


def test_owner_can_start_by_repo_id_before_clone(monkeypatch, capsys) -> None:
    calls = []
    monkeypatch.setattr("tca.codex_setup._tca_token", lambda server, name: "tca_test-token")
    monkeypatch.setattr(
        "tca.codex_setup._project_api",
        lambda url, token, path, body=None: calls.append((path, body)) or
        {"project_id": "owner/repo", "role": "m", "agent_id": "agent-m"},
    )
    project_command(argparse.Namespace(
        command="start", repo=None, project_id="OWNER/Repo",
        server_url="https://tca.example.com/mcp", token_env="TCA_TOKEN",
        replace_mcp=False,
    ))
    assert calls == [("/api/projects", {"project_id": "owner/repo"})]
    assert "Started owner/repo as M" in capsys.readouterr().out


@pytest.mark.anyio
async def test_local_mcp_runs_without_http_server_or_api_key(tmp_path: Path) -> None:
    database = tmp_path / "local.db"
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "tca.local_mcp", "--database", str(database)],
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            listed = await session.list_tools()
            assert "submit_claim" in {tool.name for tool in listed.tools}
            result = await session.call_tool("submit_claim", {"claim": {
                "claim_id": "a1-task", "project_id": "local/project", "author": "agent-a1",
                "base_commit": "abc123", "work_branch": "feature/agent-a1/a1-task",
                "goal": "Implement feature", "planned_paths": ["src/**"],
            }})
            assert result.isError is False
    # A second Codex role starts its own MCP process, yet sees the same claim.
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as manager:
            await manager.initialize()
            state = await manager.call_tool("get_claim_state", {
                "project_id": "local/project", "claim_id": "a1-task",
            })
            assert state.isError is False
            assert "a1-task" in str(state.structuredContent or state.content)
    assert database.is_file()
