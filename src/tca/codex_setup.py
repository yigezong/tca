"""Connect a Git repository to TCA through a coding-agent client."""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
import re
import shlex
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
worktree identity is in `.tca/agent-id`. Keep working in your normal coding-agent UI.
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


def legacy_skill_text() -> str:
    """The exact generated Codex skill from before multi-client support."""
    return (skill_text()
            .replace("# TCA in coding agents", "# TCA in Codex")
            .replace("your coding agent's normal chat", "Codex's normal chat")
            .replace("A trusted local turn-end adapter", "A trusted local Codex Stop hook")
            .replace("The adapter cannot infer", "The hook cannot infer")
            .replace("its latest completed agent turn", "its latest completed Codex turn")
            .replace("The resident M worker uses the locally selected coding client and polls C for new tasks.",
                     "If there is no task, tell the user and wait for the next request; a Codex chat is not an always-on daemon."))


def previous_skill_text() -> str:
    """The generated multi-client skill before configurable resident M."""
    return skill_text().replace(
        "The resident M worker uses the locally selected coding client and polls C for new tasks.",
        "The resident M worker uses Codex and polls C for new tasks.")


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
    async with stdio_client(params) as (read, write):
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
        legacy = AGENT_NOTE.replace("your normal coding-agent UI", "the normal Codex UI")
        if AGENT_NOTE.strip() not in existing and legacy.strip() not in existing:
            raise SetupError(f"Existing TCA block in {path} differs; inspect it before setup")
        if legacy.strip() in existing:
            path.write_text(existing.replace(legacy.strip(), AGENT_NOTE.strip()), encoding="utf-8")
        return
    path.write_text(existing.rstrip() + ("\n\n" if existing.strip() else "") + AGENT_NOTE,
                    encoding="utf-8")


def ensure_ignore(repo: Path, client: str = "codex") -> None:
    path = repo / ".gitignore"
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    entries = [".tca/agent-id", ".tca/manager-client", ".codex/hooks.json"]
    entries += {"pi": [".pi/mcp.json", ".pi/extensions/tca.ts"],
                "opencode": [".opencode/opencode.json", ".opencode/plugins/tca/",
                             ".opencode/plugin/tca.ts"],
                "dsh": [".tca/dsh.patch.yml", ".tca/dsh-hooks.json"]}.get(client, [])
    missing = [entry for entry in entries
               if entry not in existing.splitlines()]
    if missing:
        path.write_text(existing.rstrip() + ("\n" if existing.strip() else "")
                        + "\n".join(missing) + "\n", encoding="utf-8")


def ensure_turn_hook(repo: Path) -> None:
    """Install a local, user-trusted Stop hook without editing other hooks."""
    path = repo / ".codex" / "hooks.json"
    config = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if not isinstance(config, dict):
        raise SetupError(f"Invalid hook configuration in {path}")
    hooks = config.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise SetupError(f"Invalid hook configuration in {path}")
    stop = hooks.setdefault("Stop", [])
    if not isinstance(stop, list):
        raise SetupError(f"Invalid Stop hooks in {path}")
    command = shlex.quote(sys.executable) + " -m tca.turn_hook"
    for group in stop:
        if not isinstance(group, dict):
            continue
        for item in group.get("hooks", []):
            if isinstance(item, dict) and "tca.turn_hook" in str(item.get("command", "")):
                if item.get("command") != command:
                    item["command"] = command
                    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
                return
    stop.append({"hooks": [{"type": "command", "command": command,
                             "timeout": 30, "statusMessage": "Capturing TCA worktree summary"}]})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


def setup(args: argparse.Namespace) -> None:
    client = getattr(args, "client", "codex")
    if client == "codex" and not shutil.which("codex"):
        raise SetupError("Codex CLI is not on PATH; install/open Codex CLI first")
    if client != "codex" and not shutil.which(client):
        raise SetupError(f"{client} CLI is not on PATH")
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
                    in {"TCA_GITHUB_TOKEN", "TCA_API_TOKEN", args.token_env}
            )
        )
    )
    overwrite_paths = {project_path, identity_path} if migrating_config else set()
    if skill_path.exists():
        existing_skill = skill_path.read_text(encoding="utf-8")
        if existing_skill in {legacy_skill_text(), previous_skill_text()} or (migrating_config
                and existing_skill.startswith("---\nname: tca-coordination\n")
                and "# TCA in Codex" in existing_skill):
            overwrite_paths.add(skill_path)
    for path, content in ((project_path, project_text), (skill_path, skill_text()),
                          (identity_path, args.agent_id + "\n")):
        if path.exists() and path.read_text(encoding="utf-8") != content and path not in overwrite_paths:
            raise SetupError(f"Refusing to overwrite {path}; inspect it before setup")
    agent_path = repo / "AGENTS.md"
    if agent_path.exists() and START in agent_path.read_text(encoding="utf-8"):
        old_note = AGENT_NOTE.replace("your normal coding-agent UI", "the normal Codex UI")
        if not any(note.strip() in agent_path.read_text(encoding="utf-8")
                   for note in (AGENT_NOTE, old_note)):
            raise SetupError(f"Existing TCA block in {agent_path} differs; inspect it before setup")
    if client == "codex":
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
    ensure_ignore(repo, client)
    if args.agent_id == "agent-m":
        (repo / ".tca" / "manager-client").write_text(client + "\n", encoding="utf-8")
        if client != "codex":
            from .agent_adapters import install_adapter
            install_adapter(client, repo, mcp_name, desired, capture=False)
    if args.agent_id != "agent-m":
        if client == "codex":
            ensure_turn_hook(repo)
        else:
            from .agent_adapters import install_adapter
            install_adapter(client, repo, mcp_name, desired)
    print(f"TCA is connected to {client} for {repo} ({project_id}, {args.mode}).")
    print("Commit AGENTS.md, .agents/skills, .tca/project.yaml and .gitignore.")
    if args.mode == "remote":
        print(f"Make {args.token_env} available to {client} before opening it; do not commit the token.")


def doctor(args: argparse.Namespace) -> int:
    repo = git_root(args.repo)
    client = getattr(args, "client", "codex")
    issues: list[str] = []
    if not shutil.which(client):
        issues.append(f"{client} CLI is not on PATH")
    elif client == "codex":
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
        identity = ""
    else:
        identity = (repo / ".tca" / "agent-id").read_text().strip()
    if identity and identity != "agent-m":
        hook_paths = {"codex": repo / ".codex" / "hooks.json",
                      "opencode": (repo / ".opencode" / "plugins" / "tca" / "index.ts"
                                   if (repo / ".opencode" / "plugins" / "tca" / "index.ts").exists()
                                   else repo / ".opencode" / "plugin" / "tca.ts"),
                      "pi": repo / ".pi" / "extensions" / "tca.ts",
                      "dsh": repo / ".tca" / "dsh-hooks.json"}
        hook_path = hook_paths[client]
        if not hook_path.is_file() or (client == "codex" and "tca.turn_hook" not in hook_path.read_text()):
            issues.append(f"missing {client} turn capture; rerun tca-agent join (or init)")
    if not (repo / ".agents" / "skills" / "tca-coordination" / "SKILL.md").exists():
        issues.append("missing TCA skill")
    mcp_name = config.get("mcp", {}).get("server", "") if isinstance(config, dict) else ""
    current = current_transport(mcp_name) if client == "codex" and shutil.which("codex") and mcp_name else None
    if client == "codex" and current is None:
        issues.append(f"Codex has no '{mcp_name or 'tca'}' MCP server")
    elif client != "codex":
        adapter_paths = {"opencode": repo / ".opencode" / "opencode.json",
                         "pi": repo / ".pi" / "mcp.json",
                         "dsh": repo / ".tca" / "dsh.patch.yml"}
        adapter_path = adapter_paths[client]
        if not adapter_path.is_file() or mcp_name not in adapter_path.read_text():
            issues.append(f"missing {client} MCP configuration for '{mcp_name}'")
        elif client in {"pi", "opencode"} and shutil.which(client):
            listed = subprocess.run([client, "mcp", "list"], cwd=repo, text=True,
                                    capture_output=True, check=False, timeout=30)
            if listed.returncode or mcp_name not in listed.stdout or "connected" not in listed.stdout:
                issues.append(f"{client} MCP is not connected; check project trust, token, and client MCP list")
        elif client == "dsh" and shutil.which("dsh"):
            listed = subprocess.run(["dsh", "--profile", getattr(args, "dsh_profile", "web"), "--dump-config",
                                     "--patch", str(adapter_path)], cwd=repo, text=True,
                                    capture_output=True, check=False, timeout=30)
            expected = ["mcp-" + mcp_name] + ([] if identity == "agent-m" else ["tca-turn-hook"])
            if listed.returncode or any(item not in listed.stdout for item in expected):
                issues.append("dsh profile did not load the TCA patch")
        if mode == "local":
            try:
                asyncio.run(_probe_stdio(desired_transport("local", "", "TCA_TOKEN")))
            except Exception as exc:
                issues.append(f"local TCA MCP did not start: {exc}")
        elif mode == "remote":
            try:
                _tca_token(config["mcp"]["url"], config["mcp"].get("token_env", "TCA_TOKEN"))
            except (SetupError, KeyError) as exc:
                issues.append(f"TCA session unavailable: {exc}")
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
                    f"/api/projects/{urllib.parse.quote(project_id, safe='/')}/join",
                    {"role": "m" if identity == "agent-m" else "a"},
                )
                if joined.get("agent_id") != identity:
                    issues.append("local agent identity differs from GitHub project role")
            except (SetupError, OSError, KeyError) as exc:
                issues.append(f"TCA project access check failed: {exc}")
        else:
            issues.append("remote project uses a legacy token variable; migrate to TCA_TOKEN")
    for issue in issues:
        print("FAIL:", issue)
    if not issues:
        print(f"OK: {client} TCA MCP, skill, project, and identity for {repo}")
        if mode == "remote":
            print("Remote project access verified." if client == "codex" and
                  config.get("mcp", {}).get("token_env") == "TCA_TOKEN"
                  else "Remote project access was not tested by doctor.")
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
    _write_secret(path, json.dumps({**response, "key_path": str(key_path)}).encode())
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
                              f"/api/projects/{encoded}/join", {"role": args.role} if args.role else {})
        agent_id = str(result["agent_id"])
    setup(argparse.Namespace(
        repo=repo, agent_id=agent_id, project_id=project_id, mode="remote",
        server_url=args.server_url, token_env=args.token_env,
        replace_mcp=args.replace_mcp,
        replace_local_config=getattr(args, "replace_local_config", False),
        client=getattr(args, "client", "codex"),
    ))
    print(f"Joined {project_id} as {result['role'].upper()} ({agent_id}).")
    print(f"{getattr(args, 'client', 'codex')} must have {args.token_env} in its environment when it starts.")
    if agent_id == "agent-m":
        if sys.platform == "darwin":
            from .manager_service import start
            start(repo)
            print("M is running in the background; check it with 'tca-agent manager status --repo PATH'.")
        else:
            print(f"Start M with 'tca-agent manager run --repo {shlex.quote(str(repo))}' in a persistent terminal.")


def manager_command(args: argparse.Namespace) -> None:
    from .manager_service import paths, running, start, stop
    repo = git_root(args.repo)
    identity = repo / ".tca" / "agent-id"
    if not identity.exists() or identity.read_text().strip() != "agent-m":
        raise SetupError("manager commands require an agent-m worktree")
    if args.action == "run":
        from .manager_worker import serve
        selection = repo / ".tca" / "manager-client"
        client = selection.read_text(encoding="utf-8").strip() if selection.exists() else "codex"
        if client not in {"codex", "pi", "opencode", "dsh"}:
            raise SetupError(f"unsupported M client: {client}")
        client_bin = shutil.which(client)
        if not client_bin:
            raise SetupError(f"{client} CLI is not on PATH")
        asyncio.run(serve(repo, client_bin, client=client))
        return
    if args.action == "start":
        start(repo)
    elif args.action == "stop":
        stop(repo)
    elif args.action == "status":
        log_path = paths(repo)[1]
        selected = (repo / ".tca" / "manager-client")
        client = selected.read_text().strip() if selected.exists() else "codex"
        print(f"M {'running' if running(repo) else 'stopped'} ({client}) for {repo}; log: {log_path}")
        return
    print(f"M {args.action} complete for {repo}.")


def dsh_open(args: argparse.Namespace) -> None:
    """Install the profile plugins TCA needs, then open dsh with this repo's patch."""
    repo = git_root(args.repo)
    patch = repo / ".tca" / "dsh.patch.yml"
    if not patch.is_file():
        raise SetupError("missing dsh TCA patch; run 'tca-agent join --client dsh' first")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", args.profile):
        raise SetupError("invalid dsh profile name")
    binary = shutil.which("dsh")
    if not binary:
        raise SetupError("dsh CLI is not on PATH")
    version_result = subprocess.run(
        [binary, "--version"], text=True, capture_output=True,
        check=False, timeout=10,
    )
    version = version_result.stdout.strip()
    if version_result.returncode or not re.fullmatch(
        r"\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?", version
    ):
        raise SetupError("could not determine dsh version for compatible TCA plugins")
    identity_path = repo / ".tca" / "agent-id"
    if not identity_path.is_file():
        raise SetupError("missing local agent identity; run 'tca-agent join --client dsh' first")
    identity = identity_path.read_text(encoding="utf-8").strip()
    packages = ["@deepseek-ai/dsh-mcp-client"]
    if identity != "agent-m":
        packages.extend([
            "@deepseek-ai/dsh-hooks-claude-code",
            "@deepseek-ai/dsh-hook-protocol",
        ])
    profile_dir = Path(os.getenv("DSH_HOME", str(Path.home() / ".dsh"))) / "profiles" / args.profile
    package_file = profile_dir / "package.json"
    try:
        installed = json.loads(package_file.read_text()).get("dependencies", {}) if package_file.exists() else {}
    except (ValueError, OSError):
        installed = {}
    missing = [
        f"{package}@{version}" for package in packages
        if installed.get(package) != version
    ]
    if missing:
        result = subprocess.run([binary, "plugin", "--profile", args.profile, "add", "-w", *missing],
                                text=True, capture_output=True, check=False, timeout=300)
        if result.returncode:
            raise SetupError(f"Could not install dsh TCA plugins: {result.stderr.strip() or result.stdout.strip()}")
    os.execv(binary, [binary, "--profile", args.profile, "--patch", str(patch), *args.client_args])


def main() -> None:
    parser = argparse.ArgumentParser(description="Use TCA inside your coding agent")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="connect this Git repo to a coding agent and TCA")
    init.add_argument("--repo", type=Path, default=Path.cwd())
    init.add_argument("--agent-id", default="agent-a1")
    init.add_argument("--project-id")
    init.add_argument("--mode", choices=("local", "remote"), default="local")
    init.add_argument("--server-url", default="")
    init.add_argument("--token-env", default="TCA_TOKEN")
    init.add_argument("--replace-mcp", action="store_true")
    init.add_argument("--replace-local-config", action="store_true")
    init.add_argument("--client", choices=("codex", "opencode", "pi", "dsh"), default="codex")
    check = sub.add_parser("doctor", help="check Codex and TCA setup")
    check.add_argument("--repo", type=Path, default=Path.cwd())
    check.add_argument("--client", choices=("codex", "opencode", "pi", "dsh"), default="codex")
    check.add_argument("--dsh-profile", default="web")
    login = sub.add_parser("login", help="sign a challenge with a GitHub-listed SSH key")
    login.add_argument("--server-url", required=True)
    login.add_argument("--login", required=True)
    login.add_argument("--key-path", type=Path, default=tca_home() / "identity")
    session = sub.add_parser("token", help="print the local TCA-only session token")
    session.add_argument("--server-url", required=True)
    manager = sub.add_parser("manager", help="control the resident M process")
    manager.add_argument("action", choices=("start", "status", "stop", "run"))
    manager.add_argument("--repo", type=Path, default=Path.cwd())
    dsh_command = sub.add_parser("dsh-open", help="install required plugins and open dsh with TCA")
    dsh_command.add_argument("--repo", type=Path, default=Path.cwd())
    dsh_command.add_argument("--profile", default="web")
    dsh_command.add_argument("client_args", nargs="*")
    for name in ("start", "join", "projects", "delegate-m"):
        command = sub.add_parser(name, help=f"GitHub project {name}")
        if name != "projects":
            command.add_argument("--repo", type=Path,
                                 default=Path.cwd() if name == "join" else None)
            command.add_argument("--replace-mcp", action="store_true")
            command.add_argument("--replace-local-config", action="store_true")
            command.add_argument("--client", choices=("codex", "opencode", "pi", "dsh"), default="codex")
            if name == "join":
                command.add_argument("--role", choices=("a", "m"),
                                     help="choose A or M when the account has both permissions")
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
        if args.command == "manager":
            manager_command(args)
            return
        if args.command == "dsh-open":
            dsh_open(args)
            return
        if args.command in {"start", "join", "projects", "delegate-m"}:
            project_command(args)
            return
        result = setup(args) if args.command == "init" else doctor(args)
        if args.command == "init" and args.agent_id == "agent-m" and args.mode == "local":
            if sys.platform == "darwin":
                from .manager_service import start
                start(git_root(args.repo))
                print("M is running in the background; check it with 'tca-agent manager status --repo PATH'.")
            else:
                print(f"Start M with 'tca-agent manager run --repo {shlex.quote(str(git_root(args.repo)))}' in a persistent terminal.")
    except SetupError as exc:
        parser.exit(2, f"tca-codex: {exc}\n")
    if result:
        parser.exit(1)


if __name__ == "__main__":
    main()
