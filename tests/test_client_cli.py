from __future__ import annotations

import os
from pathlib import Path

from tca.client_cli import (
    ProjectConfig,
    auto_decision_for_task,
    find_repo_root,
    load_dotenv,
    load_project_config,
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
