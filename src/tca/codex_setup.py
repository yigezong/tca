"""Connect a Git repository to TCA through the user's existing Codex CLI."""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from importlib.resources import files
from pathlib import Path

import yaml
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
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
    return tca_home() / "coordination.db"


def tca_home() -> Path:
    """Allow disposable client state without changing the user's normal TCA setup."""
    override = os.getenv("TCA_HOME", "").strip()
    return Path(override).expanduser().resolve() if override else Path.home() / ".tca"


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
    migrating_config = (
        getattr(args, "replace_local_config", False)
        and args.mode == "remote"
        and isinstance(existing_project, dict)
        and str(existing_project.get("project_id", "")).lower() == project_id.lower()
        and (
            existing_project.get("mode") == "local"
            or (
                existing_project.get("mode") == "remote"
                and existing_project.get("mcp", {}).get("url") == args.server_url
                and existing_project.get("mcp", {}).get("token_env")
                    in {"TCA_GITHUB_TOKEN", "TCA_API_TOKEN"}
            )
        )
    )
    overwrite_paths = {project_path, identity_path} if migrating_config else set()
    if migrating_config and skill_path.exists():
        existing_skill = skill_path.read_text(encoding="utf-8")
        if existing_skill.startswith("---\nname: tca-coordination\n") and "# TCA in Codex" in existing_skill:
            overwrite_paths.add(skill_path)
    for path, content in ((project_path, project_text), (skill_path, skill_text()),
                          (identity_path, args.agent_id + "\n")):
        if path.exists() and path.read_text(encoding="utf-8") != content and path not in overwrite_paths:
            raise SetupError(f"Refusing to overwrite {path}; inspect it before setup")
    agent_path = repo / "AGENTS.md"
    if agent_path.exists() and START in agent_path.read_text(encoding="utf-8"):
        if AGENT_NOTE.strip() not in agent_path.read_text(encoding="utf-8"):
            raise SetupError(f"Existing TCA block in {agent_path} differs; inspect it before setup")
    ensure_mcp(mcp_name, desired, args.replace_mcp)
    if project_path in overwrite_paths:
        project_path.write_text(project_text, encoding="utf-8")
    else:
        ensure_file(project_path, project_text)
    if skill_path in overwrite_paths:
        skill_path.write_text(skill_text(), encoding="utf-8")
    else:
        ensure_file(skill_path, skill_text())
    if identity_path in overwrite_paths:
        identity_path.write_text(args.agent_id + "\n", encoding="utf-8")
    else:
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
    elif mode == "remote":
        token_env = config.get("mcp", {}).get("token_env", "TCA_TOKEN")
        token = os.getenv(token_env, "")
        if not token:
            issues.append("TCA token environment variable is unavailable to this shell")
        elif token_env == "TCA_TOKEN":
            try:
                project_id = config.get("project_id", "")
                joined = _project_api(
                    config["mcp"]["url"], token,
                    f"/api/projects/{urllib.parse.quote(project_id, safe='/')}/join", {},
                )
                identity = (repo / ".tca" / "agent-id").read_text().strip()
                if joined.get("agent_id") != identity:
                    issues.append("local agent identity differs from GitHub project role")
            except (SetupError, OSError, KeyError) as exc:
                issues.append(f"TCA project access check failed: {exc}")
        else:
            issues.append("remote project uses a legacy token variable; migrate to TCA_TOKEN")
    for issue in issues:
        print("FAIL:", issue)
    if not issues:
        print(f"OK: Codex login, TCA MCP, skill, project, and identity for {repo}")
        if mode == "remote":
            print("Remote project access verified." if config.get("mcp", {}).get("token_env") == "TCA_TOKEN"
                  else "Remote connectivity and token validity were not tested.")
    return bool(issues)


def _session_path(server_url: str) -> Path:
    digest = hashlib.sha256(server_url.encode()).hexdigest()[:24]
    return tca_home() / "sessions" / f"{digest}.json"


def _write_secret(path: Path, content: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        os.fchmod(descriptor, 0o600)
        output.write(content)


def _tca_token(server_url: str, token_env: str) -> str:
    token = os.getenv(token_env, "").strip()
    if not token:
        path = _session_path(server_url)
        if path.exists():
            token = str(json.loads(path.read_text())["token"])
    if not token.startswith("tca_"):
        raise SetupError("Run 'tca-codex login --server-url URL --login GITHUB_LOGIN' first")
    return token


def login_command(args: argparse.Namespace) -> None:
    key_path = args.key_path.expanduser().resolve()
    key_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not key_path.exists():
        key = ed25519.Ed25519PrivateKey.generate()
        _write_secret(key_path, key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH,
            serialization.NoEncryption(),
        ))
    else:
        key = serialization.load_ssh_private_key(key_path.read_bytes(), password=None)
        if not isinstance(key, ed25519.Ed25519PrivateKey):
            raise SetupError("TCA login requires an Ed25519 SSH key")
    public_key = key.public_key().public_bytes(
        serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH
    ).decode()
    challenge = _project_api(args.server_url, "", "/api/auth/challenge",
                             {"login": args.login})
    signed = f"tca-login-v1\n{challenge['challenge_id']}\n{challenge['nonce']}".encode()
    try:
        response = _project_api(args.server_url, "", "/api/auth/login", {
            "login": args.login, "challenge_id": challenge["challenge_id"],
            "public_key": public_key,
            "signature": base64.b64encode(key.sign(signed)).decode(),
        })
    except SetupError as exc:
        if "SSH public key is not registered on GitHub" in str(exc):
            raise SetupError(
                "Add this public key to https://github.com/settings/keys, then retry:\n"
                + public_key
            ) from exc
        raise
    path = _session_path(args.server_url)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    _write_secret(path, json.dumps(response).encode())
    print(f"TCA login complete for {response['login']}; session expires at {response['expires_at']}.")
    print(f"To expose the TCA-only session to Codex: export TCA_TOKEN=\"$(tca-codex token --server-url {args.server_url})\"")


def _project_api(server_url: str, token: str, path: str,
                 body: dict | None = None) -> dict:
    if not server_url.startswith("https://") or not server_url.endswith("/mcp"):
        raise SetupError("server URL must be an https://.../mcp endpoint")
    url = server_url.removesuffix("/mcp") + path
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(
        url, data=json.dumps(body).encode() if body is not None else None,
        headers=headers, method="POST" if body is not None else "GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SetupError(f"TCA project request failed ({exc.code}): {detail}") from exc
    except urllib.error.URLError as exc:
        raise SetupError(f"Cannot reach TCA server: {exc.reason}") from exc
    if not isinstance(result, dict):
        raise SetupError("TCA server returned an invalid response")
    return result


def project_command(args: argparse.Namespace) -> None:
    token = _tca_token(args.server_url, args.token_env)
    if args.command == "projects":
        result = _project_api(args.server_url, token, "/api/projects")
        for project in result.get("projects", []):
            print(f"{project['project_id']}  {project['role'].upper()}  owner={project['owner']}")
        return
    repo = git_root(args.repo) if args.repo else None
    requested_id = getattr(args, "project_id", None)
    project_id = (requested_id or project_id_for(repo)).lower() if repo or requested_id else ""
    if project_id.startswith("local/"):
        raise SetupError("repo needs a GitHub origin remote to start or join")
    if not project_id:
        raise SetupError("start requires --repo or --project-id")
    if requested_id and repo and project_id != project_id_for(repo).lower():
        raise SetupError("--project-id does not match the repo's GitHub origin")
    if args.command == "start":
        result = _project_api(args.server_url, token, "/api/projects",
                              {"project_id": project_id})
        agent_id = str(result["agent_id"])
        if repo is None:
            print(f"Started {project_id} as {result['role'].upper()}. Clone the repo, then run 'tca-codex join --repo PATH' to configure Codex.")
            return
    elif args.command == "delegate-m":
        if repo is None and not requested_id:
            raise SetupError("delegate-m requires --repo or --project-id")
        encoded = urllib.parse.quote(project_id, safe="/")
        result = _project_api(args.server_url, token,
                              f"/api/projects/{encoded}/manager", {"login": args.login})
        print(f"M for {project_id} is now {result['manager']}.")
        return
    else:
        if repo is None:
            raise SetupError("join requires --repo")
        encoded = urllib.parse.quote(project_id, safe="/")
        result = _project_api(args.server_url, token,
                              f"/api/projects/{encoded}/join", {})
        agent_id = str(result["agent_id"])
    setup(argparse.Namespace(
        repo=repo, agent_id=agent_id, project_id=project_id, mode="remote",
        server_url=args.server_url, token_env=args.token_env,
        replace_mcp=args.replace_mcp,
        replace_local_config=getattr(args, "replace_local_config", False),
    ))
    print(f"Joined {project_id} as {result['role'].upper()} ({agent_id}).")
    print(f"Codex must have {args.token_env} in its environment when it starts.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Use TCA inside your existing Codex")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="connect this Git repo to Codex and TCA")
    init.add_argument("--repo", type=Path, default=Path.cwd())
    init.add_argument("--agent-id", default="agent-a1")
    init.add_argument("--project-id")
    init.add_argument("--mode", choices=("local", "remote"), default="local")
    init.add_argument("--server-url", default="")
    init.add_argument("--token-env", default="TCA_TOKEN")
    init.add_argument("--replace-mcp", action="store_true")
    init.add_argument("--replace-local-config", action="store_true")
    check = sub.add_parser("doctor", help="check Codex and TCA setup")
    check.add_argument("--repo", type=Path, default=Path.cwd())
    login = sub.add_parser("login", help="sign a challenge with a GitHub-listed SSH key")
    login.add_argument("--server-url", required=True)
    login.add_argument("--login", required=True)
    login.add_argument("--key-path", type=Path, default=tca_home() / "identity")
    session = sub.add_parser("token", help="print the local TCA-only session token")
    session.add_argument("--server-url", required=True)
    for name in ("start", "join", "projects", "delegate-m"):
        command = sub.add_parser(name, help=f"GitHub project {name}")
        if name != "projects":
            command.add_argument("--repo", type=Path,
                                 default=Path.cwd() if name == "join" else None)
            command.add_argument("--replace-mcp", action="store_true")
            command.add_argument("--replace-local-config", action="store_true")
            if name in {"start", "delegate-m"}:
                command.add_argument("--project-id", help="GitHub owner/repo; no local clone required")
            if name == "delegate-m":
                command.add_argument("--login", required=True, help="GitHub login of the new M")
        command.add_argument("--server-url", required=True)
        command.add_argument("--token-env", default="TCA_TOKEN")
    args = parser.parse_args()
    try:
        if args.command == "login":
            login_command(args)
            return
        if args.command == "token":
            print(_tca_token(args.server_url, "TCA_TOKEN"))
            return
        if args.command in {"start", "join", "projects", "delegate-m"}:
            project_command(args)
            return
        result = setup(args) if args.command == "init" else doctor(args)
    except SetupError as exc:
        parser.exit(2, f"tca-codex: {exc}\n")
    if result:
        parser.exit(1)


if __name__ == "__main__":
    main()
