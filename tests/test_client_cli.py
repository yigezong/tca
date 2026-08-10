from __future__ import annotations

import os
from argparse import Namespace
from pathlib import Path

import pytest

from tca.client_cli import (
    ProjectConfig,
    TcaClientError,
    auto_decision_for_task,
    find_repo_root,
    load_dotenv,
    load_project_config,
    resolve_project_config,
    resolve_repo_root,
)


def write_project_config(repo: Path) -> None:
    tca_dir = repo / ".tca"
    tca_dir.mkdir()
    (tca_dir / "project.yaml").write_text(
        "\n".join(
            [
                "version: 1",
                "project_id: owner/repo",
                "mcp:",
                "  url: https://tca.example.com/mcp",
                "  token_env: TCA_API_TOKEN",
                "",
            ]
        )
    )


def test_find_repo_root_from_nested_directory(tmp_path: Path) -> None:
    write_project_config(tmp_path)
    nested = tmp_path / "src" / "pkg"
    nested.mkdir(parents=True)

    assert find_repo_root(nested) == tmp_path


def test_load_project_config_reads_project_yaml(tmp_path: Path) -> None:
    write_project_config(tmp_path)

    assert load_project_config(tmp_path) == ProjectConfig(
        project_id="owner/repo",
        mcp_url="https://tca.example.com/mcp",
        token_env="TCA_API_TOKEN",
    )


def test_load_dotenv_preserves_existing_values(tmp_path: Path, monkeypatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("TCA_API_TOKEN=file-token\nTCA_AGENT_ID=agent-a1\n")
    monkeypatch.setenv("TCA_API_TOKEN", "existing-token")

    load_dotenv(env_file)

    assert os.environ["TCA_API_TOKEN"] == "existing-token"
    assert os.environ["TCA_AGENT_ID"] == "agent-a1"


def test_m_commands_can_resolve_config_without_repo_root(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TCA_PROJECT_ID", "owner/repo")
    monkeypatch.setenv("TCA_MCP_URL", "https://tca.example.com/mcp")
    args = Namespace(
        command="watch",
        repo_root=None,
        project_id=None,
        mcp_url=None,
        token_env=None,
    )

    repo_root = resolve_repo_root(args)

    assert repo_root is None
    assert resolve_project_config(args, repo_root) == ProjectConfig(
        project_id="owner/repo",
        mcp_url="https://tca.example.com/mcp",
        token_env="TCA_API_TOKEN",
    )


def test_submit_requires_repo_root(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    args = Namespace(command="submit", repo_root=None)

    with pytest.raises(TcaClientError, match="could not find"):
        resolve_repo_root(args)


def test_auto_decision_approves_when_paths_do_not_overlap() -> None:
    decision = auto_decision_for_task(
        {
            "claim": {
                "claim": {
                    "claim_id": "claim-1",
                    "planned_paths": ["src/greeting.py"],
                }
            },
            "active_claims": [
                {
                    "claim": {
                        "claim_id": "claim-2",
                        "planned_paths": ["docs/readme.md"],
                    }
                }
            ],
        }
    )

    assert decision["state"] == "approved"
    assert decision["wait_for"] == []


def test_auto_decision_waits_for_overlapping_active_claims() -> None:
    decision = auto_decision_for_task(
        {
            "claim": {
                "claim": {
                    "claim_id": "claim-1",
                    "planned_paths": ["src/greeting.py"],
                }
            },
            "active_claims": [
                {
                    "claim": {
                        "claim_id": "claim-2",
                        "planned_paths": ["src/**"],
                    }
                }
            ],
        }
    )

    assert decision["state"] == "waiting"
    assert decision["wait_for"] == ["claim-2"]
    assert decision["conflicts_with"] == ["claim-2"]


def test_auto_decision_constrains_overlapping_completed_claims() -> None:
    decision = auto_decision_for_task(
        {
            "claim": {
                "claim": {
                    "claim_id": "claim-2",
                    "planned_paths": ["src/greeting.py"],
                }
            },
            "active_claims": [],
            "completed_claims": [
                {
                    "state": "completed",
                    "claim": {
                        "claim_id": "claim-1",
                        "work_branch": "feature/agent-a1/claim-1",
                        "planned_paths": ["src/**"],
                    },
                }
            ],
        }
    )

    assert decision["state"] == "approved_with_constraints"
    assert decision["wait_for"] == []
    assert decision["conflicts_with"] == ["claim-1"]
    assert "feature/agent-a1/claim-1" in decision["constraints"][0]
