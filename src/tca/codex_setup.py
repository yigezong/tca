"""Connect a Git repository to TCA through the user's existing Codex CLI."""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import re
import shutil
import subprocess
import sys
from importlib.resources import files
from pathlib import Path

import yaml
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


LOCAL_MCP_NAME = "tca_local"
REMOTE_MCP_NAME = "tca"
START = "<!-- tca-codex:start -->"
END = "<!-- tca-codex:end -->"
AGENT_NOTE = f"""{START}
This repository uses TCA for parallel coding. Before changing shared code, read
`.agents/skills/tca-coordination/SKILL.md` and `.tca/project.yaml`. The local
worktree identity is in `.tca/agent-id`. Keep working in the normal Codex UI.
{END}
"""


class SetupError(RuntimeError):
    pass


def run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(args, text=True, capture_output=True, check=False)
    if check and result.returncode:
        raise SetupError(result.stderr.strip() or result.stdout.strip() or f"failed: {args[0]}")
    return result


def git_root(path: Path) -> Path:
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
        text=True, capture_output=True, check=False,
    )
    if result.returncode:
        raise SetupError(f"{path} is not a Git repository")
    return Path(result.stdout.strip()).resolve()


def project_id_for(repo: Path) -> str:
    remote = run("git", "-C", str(repo), "remote", "get-url", "origin", check=False)
    url = remote.stdout.strip().rstrip("/")
    match = re.search(r"(?:github\.com[:/])([^/\s]+/[^/\s]+?)(?:\.git)?$", url)
    if match:
        return match.group(1)
    return f"local/{repo.name}"


def local_db() -> Path:
    return Path.home() / ".tca" / "coordination.db"


def skill_text() -> str:
    return files("tca").joinpath("assets", "tca-skill.md").read_text(encoding="utf-8")


def desired_transport(mode: str, server_url: str, token_env: str) -> dict:
    if mode == "local":
        return {
            "type": "stdio",
            "command": sys.executable,
            "args": ["-m", "tca.local_mcp", "--database", str(local_db())],
        }
    if not server_url.startswith("https://"):
        raise SetupError("remote TCA MCP URL must start with https://")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", token_env):
        raise SetupError("invalid token environment variable name")
    return {"type": "streamable_http", "url": server_url,
            "bearer_token_env_var": token_env}


def current_transport(name: str) -> dict | None:
    result = run("codex", "mcp", "get", name, "--json", check=False)
    if result.returncode:
        return None
    return json.loads(result.stdout)["transport"]


def transport_matches(current: dict, desired: dict) -> bool:
    return all(current.get(key) == value for key, value in desired.items())


async def _probe_stdio(transport: dict) -> None:
    params = StdioServerParameters(
        command=transport["command"], args=transport.get("args") or [],
    )
    async with stdio_client(params, errlog=io.StringIO()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            names = {tool.name for tool in (await session.list_tools()).tools}
            if not {"submit_claim", "wait_for_decision", "wait_for_coordination_task"} <= names:
                raise SetupError("TCA MCP started but required coordination tools are missing")


def ensure_mcp(name: str, desired: dict, replace: bool) -> None:
    current = current_transport(name)
    if current is not None and transport_matches(current, desired):
        return
    if current is not None and not replace:
        raise SetupError(f"Codex already has a different '{name}' MCP server. Use --replace-mcp to replace it.")
    if desired["type"] == "stdio":
        run("codex", "mcp", "add", name, "--", desired["command"], *desired["args"])
    else:
        run("codex", "mcp", "add", name, "--url", desired["url"],
            "--bearer-token-env-var", desired["bearer_token_env_var"])


def ensure_file(path: Path, content: str) -> None:
    if path.exists():
        if path.read_text(encoding="utf-8") != content:
            raise SetupError(f"Refusing to overwrite {path}; inspect it before setup")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def ensure_agent_note(repo: Path) -> None:
    path = repo / "AGENTS.md"
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if START in existing:
        if AGENT_NOTE.strip() not in existing:
            raise SetupError(f"Existing TCA block in {path} differs; inspect it before setup")
        return
    path.write_text(existing.rstrip() + ("\n\n" if existing.strip() else "") + AGENT_NOTE,
                    encoding="utf-8")


def ensure_ignore(repo: Path) -> None:
    path = repo / ".gitignore"
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    entry = ".tca/agent-id"
    if entry in existing.splitlines():
        return
    path.write_text(existing.rstrip() + ("\n" if existing.strip() else "") + entry + "\n",
                    encoding="utf-8")


def setup(args: argparse.Namespace) -> None:
    if not shutil.which("codex"):
        raise SetupError("Codex CLI is not on PATH; install/open Codex CLI first")
    repo = git_root(args.repo)
    if not re.fullmatch(r"agent-[A-Za-z0-9_-]+", args.agent_id):
        raise SetupError("agent ID must look like agent-a1 or agent-m")
    desired = desired_transport(args.mode, args.server_url, args.token_env)
    mcp_name = LOCAL_MCP_NAME if args.mode == "local" else REMOTE_MCP_NAME
    project_path = repo / ".tca" / "project.yaml"
    existing_project = (
        yaml.safe_load(project_path.read_text(encoding="utf-8"))
        if project_path.exists() else None
    )
    if existing_project is not None and not isinstance(existing_project, dict):
        raise SetupError(f"Invalid project configuration in {project_path}")
    project_id = args.project_id or (existing_project or {}).get("project_id") or project_id_for(repo)
    project = {"version": 1, "project_id": project_id, "mode": args.mode,
               "mcp": {"server": mcp_name}}
    if args.mode == "remote":
        project["mcp"].update({"url": args.server_url, "token_env": args.token_env})
    project_text = yaml.safe_dump(project, sort_keys=False, allow_unicode=True)
    skill_path = repo / ".agents" / "skills" / "tca-coordination" / "SKILL.md"
    identity_path = repo / ".tca" / "agent-id"
    for path, content in ((project_path, project_text), (skill_path, skill_text()),
                          (identity_path, args.agent_id + "\n")):
        if path.exists() and path.read_text(encoding="utf-8") != content:
            raise SetupError(f"Refusing to overwrite {path}; inspect it before setup")
    agent_path = repo / "AGENTS.md"
    if agent_path.exists() and START in agent_path.read_text(encoding="utf-8"):
        if AGENT_NOTE.strip() not in agent_path.read_text(encoding="utf-8"):
            raise SetupError(f"Existing TCA block in {agent_path} differs; inspect it before setup")
    ensure_mcp(mcp_name, desired, args.replace_mcp)
    ensure_file(project_path, project_text)
    ensure_file(skill_path, skill_text())
    ensure_file(identity_path, args.agent_id + "\n")
    ensure_agent_note(repo)
    ensure_ignore(repo)
    print(f"TCA is connected to Codex for {repo} ({project_id}, {args.mode}).")
    print("Use Codex normally in this repository. Commit AGENTS.md, .agents/skills, .tca/project.yaml and .gitignore.")
    if args.mode == "remote":
        print(f"Make {args.token_env} available to Codex before opening it; do not commit the token.")


def doctor(args: argparse.Namespace) -> int:
    repo = git_root(args.repo)
    issues: list[str] = []
    if not shutil.which("codex"):
        issues.append("Codex CLI is not on PATH")
    else:
        login = run("codex", "login", "status", check=False)
        if login.returncode:
            issues.append("Codex is not signed in; run 'codex login'")
    config_path = repo / ".tca" / "project.yaml"
    if not config_path.exists():
        issues.append("missing .tca/project.yaml; run 'tca-codex init'")
        config = {}
    else:
        parsed = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        config = parsed if isinstance(parsed, dict) else {}
        if not isinstance(parsed, dict):
            issues.append("invalid .tca/project.yaml")
    mode = config.get("mode", "")
    if mode not in {"local", "remote"}:
        issues.append("project mode must be local or remote")
    if not (repo / ".tca" / "agent-id").exists():
        issues.append("missing local .tca/agent-id")
    if not (repo / ".agents" / "skills" / "tca-coordination" / "SKILL.md").exists():
        issues.append("missing TCA skill")
    mcp_name = config.get("mcp", {}).get("server", "") if isinstance(config, dict) else ""
    current = current_transport(mcp_name) if shutil.which("codex") and mcp_name else None
    if current is None:
        issues.append(f"Codex has no '{mcp_name or 'tca'}' MCP server")
    elif mode == "local":
        if current.get("type") != "stdio":
            issues.append("project is local but Codex TCA MCP is not stdio")
        else:
            try:
                asyncio.run(_probe_stdio(current))
            except Exception as exc:
                issues.append(f"local TCA MCP did not start: {exc}")
    elif mode == "remote" and current.get("url") != config.get("mcp", {}).get("url"):
        issues.append("Codex TCA MCP URL differs from project configuration")
    elif mode == "remote" and not os.getenv(config.get("mcp", {}).get("token_env", "TCA_API_TOKEN")):
        issues.append("TCA token environment variable is unavailable to this shell")
    for issue in issues:
        print("FAIL:", issue)
    if not issues:
        print(f"OK: Codex login, TCA MCP, skill, project, and identity for {repo}")
        if mode == "remote":
            print("Remote connectivity and token validity were not tested.")
    return bool(issues)


def main() -> None:
    parser = argparse.ArgumentParser(description="Use TCA inside your existing Codex")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="connect this Git repo to Codex and TCA")
    init.add_argument("--repo", type=Path, default=Path.cwd())
    init.add_argument("--agent-id", default="agent-a1")
    init.add_argument("--project-id")
    init.add_argument("--mode", choices=("local", "remote"), default="local")
    init.add_argument("--server-url", default="")
    init.add_argument("--token-env", default="TCA_API_TOKEN")
    init.add_argument("--replace-mcp", action="store_true")
    check = sub.add_parser("doctor", help="check Codex and TCA setup")
    check.add_argument("--repo", type=Path, default=Path.cwd())
    args = parser.parse_args()
    try:
        result = setup(args) if args.command == "init" else doctor(args)
    except SetupError as exc:
        parser.exit(2, f"tca-codex: {exc}\n")
    if result:
        parser.exit(1)


if __name__ == "__main__":
    main()
