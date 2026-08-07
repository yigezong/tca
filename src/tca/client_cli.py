from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .rules import paths_overlap


DEFAULT_TOKEN_ENV = "TCA_API_TOKEN"
REPO_REQUIRED_COMMANDS = {"submit"}


class TcaClientError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProjectConfig:
    project_id: str
    mcp_url: str
    token_env: str = DEFAULT_TOKEN_ENV


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


def find_repo_root(start: Path) -> Path:
    current = start.resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / ".tca" / "project.yaml").is_file():
            return candidate
    raise TcaClientError("could not find .tca/project.yaml")


def load_project_config(repo_root: Path) -> ProjectConfig:
    config_path = repo_root / ".tca" / "project.yaml"
    if not config_path.exists():
        raise TcaClientError(f"missing {config_path}")

    raw = yaml.safe_load(config_path.read_text()) or {}
    if not isinstance(raw, Mapping):
        raise TcaClientError(f"{config_path} must contain a mapping")

    mcp = raw.get("mcp") or {}
    if not isinstance(mcp, Mapping):
        raise TcaClientError(f"{config_path} mcp field must contain a mapping")

    project_id = raw.get("project_id")
    mcp_url = mcp.get("url")
    token_env = mcp.get("token_env") or DEFAULT_TOKEN_ENV

    if not isinstance(project_id, str) or not project_id.strip():
        raise TcaClientError(f"{config_path} must define project_id")
    if not isinstance(mcp_url, str) or not mcp_url.strip():
        raise TcaClientError(f"{config_path} must define mcp.url")
    if not isinstance(token_env, str) or not token_env.strip():
        raise TcaClientError(f"{config_path} mcp.token_env must be a string")

    return ProjectConfig(
        project_id=project_id.strip(),
        mcp_url=mcp_url.strip(),
        token_env=token_env.strip(),
    )


def resolve_repo_root(args: argparse.Namespace) -> Path | None:
    if args.repo_root:
        return args.repo_root.resolve()
    try:
        return find_repo_root(Path.cwd())
    except TcaClientError:
        if args.command in REPO_REQUIRED_COMMANDS:
            raise
        return None


def resolve_project_config(
    args: argparse.Namespace,
    repo_root: Path | None,
) -> ProjectConfig:
    config = load_project_config(repo_root) if repo_root else None
    project_id = (
        args.project_id
        or os.getenv("TCA_PROJECT_ID")
        or (config.project_id if config else "")
    )
    mcp_url = (
        args.mcp_url
        or os.getenv("TCA_MCP_URL")
        or (config.mcp_url if config else "")
    )
    token_env = args.token_env or (config.token_env if config else DEFAULT_TOKEN_ENV)

    if not project_id:
        raise TcaClientError(
            "project_id is required; pass --project-id, set TCA_PROJECT_ID, "
            "or run from a repo with .tca/project.yaml"
        )
    if not mcp_url:
        raise TcaClientError(
            "mcp_url is required; pass --mcp-url, set TCA_MCP_URL, "
            "or run from a repo with .tca/project.yaml"
        )
    return ProjectConfig(project_id=project_id, mcp_url=mcp_url, token_env=token_env)


def decode_tool_result(result: object) -> dict[str, Any] | None:
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, Mapping):
        value = dict(structured)
        return value.get("result") if set(value) == {"result"} else value
    for item in getattr(result, "content", []):
        text = getattr(item, "text", None)
        if not isinstance(text, str):
            continue
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(value, Mapping):
            result_value = dict(value)
            return result_value.get("result") if set(result_value) == {"result"} else result_value
        if value is None:
            return None
    raise TcaClientError("TCA tool returned no structured object")


async def call_tool(
    *,
    mcp_url: str,
    token: str,
    name: str,
    arguments: dict[str, object],
) -> dict[str, Any] | None:
    import httpx
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    headers = {"Authorization": f"Bearer {token}"}
    timeout = httpx.Timeout(30.0, read=60.0)
    async with httpx.AsyncClient(
        headers=headers,
        timeout=timeout,
        follow_redirects=True,
    ) as http_client:
        async with streamable_http_client(
            mcp_url,
            http_client=http_client,
            terminate_on_close=False,
        ) as streams:
            read_stream, write_stream = streams[:2]
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.call_tool(name, arguments)
    if getattr(result, "isError", False):
        raise TcaClientError(f"TCA tool {name} failed: {result}")
    return decode_tool_result(result)


def load_claim(path: Path, project_id: str) -> dict[str, object]:
    value = yaml.safe_load(path.read_text()) or {}
    if not isinstance(value, Mapping):
        raise TcaClientError(f"{path} must contain a mapping")
    claim = dict(value)
    claim.setdefault("project_id", project_id)
    return claim


def print_json(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True), flush=True)


def auto_decision_for_task(task: Mapping[str, Any]) -> dict[str, object]:
    claim_record = task.get("claim")
    if not isinstance(claim_record, Mapping):
        raise TcaClientError("coordination task is missing claim record")
    claim = claim_record.get("claim")
    if not isinstance(claim, Mapping):
        raise TcaClientError("coordination task claim record is missing claim")

    planned_paths = [str(path) for path in claim.get("planned_paths", ())]
    conflicts: list[str] = []
    constraints: list[str] = []
    for raw_record in task.get("active_claims", ()):
        if not isinstance(raw_record, Mapping):
            continue
        active_claim = raw_record.get("claim")
        if not isinstance(active_claim, Mapping):
            continue
        active_paths = [str(path) for path in active_claim.get("planned_paths", ())]
        if any(
            paths_overlap(candidate, occupied)
            for candidate in planned_paths
            for occupied in active_paths
        ):
            claim_id = str(active_claim.get("claim_id", ""))
            if claim_id:
                conflicts.append(claim_id)
            constraints.extend(active_paths)

    if conflicts:
        return {
            "state": "waiting",
            "reason": "planned paths overlap active claims",
            "constraints": sorted(set(constraints)),
            "wait_for": conflicts,
            "conflicts_with": conflicts,
        }
    return {
        "state": "approved",
        "reason": "No active path conflicts among active claims.",
        "constraints": [],
        "wait_for": [],
        "conflicts_with": [],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="TCA MCP client for A/M workflows.")
    parser.add_argument("--repo-root", type=Path, help="Repo containing .tca/project.yaml.")
    parser.add_argument("--mcp-url", help="Override .tca/project.yaml mcp.url.")
    parser.add_argument("--project-id", help="Override .tca/project.yaml project_id.")
    parser.add_argument("--token-env", help="Override .tca/project.yaml mcp.token_env.")
    parser.add_argument(
        "--env-file",
        action="append",
        default=[],
        type=Path,
        help="Extra dotenv file to load before .env files in cwd and repo root.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    submit = subparsers.add_parser("submit", help="A/adapter: submit a Claim directly.")
    submit.add_argument("--claim-file", type=Path, default=Path(".tca/claim.yaml"))

    task = subparsers.add_parser("task", help="M: wait for a coordination task.")
    task.add_argument("--timeout", type=float, default=30.0)

    watch = subparsers.add_parser(
        "watch", help="M: continuously wait for and auto-decide coordination tasks."
    )
    watch.add_argument("--timeout", type=float, default=30.0)
    watch.add_argument("--max-tasks", type=int, default=0)
    watch.add_argument(
        "--idle-exit",
        action="store_true",
        help="Exit after the first idle timeout instead of waiting forever.",
    )

    approve = subparsers.add_parser("approve", help="M: submit a coordination decision.")
    approve.add_argument("claim_id")
    approve.add_argument(
        "--state",
        default="approved",
        choices=[
            "approved",
            "approved_with_constraints",
            "waiting",
            "blocked",
            "needs_human",
        ],
    )
    approve.add_argument("--reason", default="approved by TCA coordinator")
    approve.add_argument("--constraint", action="append", default=[])
    approve.add_argument("--wait-for", action="append", default=[])
    approve.add_argument("--conflicts-with", action="append", default=[])

    state = subparsers.add_parser("state", help="A/M: get claim state.")
    state.add_argument("claim_id")

    wait = subparsers.add_parser("wait", help="A: wait for claim decision.")
    wait.add_argument("claim_id")
    wait.add_argument("--timeout", type=float, default=30.0)

    start = subparsers.add_parser("start", help="A: mark claim executing.")
    start.add_argument("claim_id")

    complete = subparsers.add_parser("complete", help="A: mark claim completed.")
    complete.add_argument("claim_id")

    return parser


async def run(args: argparse.Namespace) -> dict[str, Any] | None:
    repo_root = resolve_repo_root(args)
    for env_file in args.env_file:
        load_dotenv(env_file.resolve())
    load_dotenv(Path.cwd() / ".env")
    if repo_root:
        load_dotenv(repo_root / ".env")
    config = resolve_project_config(args, repo_root)

    project_id = config.project_id
    mcp_url = config.mcp_url
    token_env = config.token_env
    token = os.getenv(token_env, "")
    if not token:
        locations = "the environment"
        if repo_root:
            locations += f" or {repo_root / '.env'}"
        raise TcaClientError(f"{token_env} is required; set it in {locations}")

    if args.command == "submit":
        if repo_root is None:
            raise TcaClientError("submit requires --repo-root or .tca/project.yaml")
        claim_file = args.claim_file
        if not claim_file.is_absolute():
            claim_file = repo_root / claim_file
        return await call_tool(
            mcp_url=mcp_url,
            token=token,
            name="submit_claim",
            arguments={"claim": load_claim(claim_file, project_id)},
        )
    if args.command == "task":
        return await call_tool(
            mcp_url=mcp_url,
            token=token,
            name="wait_for_coordination_task",
            arguments={
                "project_id": project_id,
                "timeout_seconds": args.timeout,
                "poll_seconds": 0.25,
            },
        )
    if args.command == "watch":
        processed: list[dict[str, Any]] = []
        while args.max_tasks <= 0 or len(processed) < args.max_tasks:
            task = await call_tool(
                mcp_url=mcp_url,
                token=token,
                name="wait_for_coordination_task",
                arguments={
                    "project_id": project_id,
                    "timeout_seconds": args.timeout,
                    "poll_seconds": 0.25,
                },
            )
            if task is None:
                event = {"event": "idle", "project_id": project_id}
                print_json(event)
                if args.idle_exit:
                    break
                continue

            decision = auto_decision_for_task(task)
            claim_record = task.get("claim")
            if not isinstance(claim_record, Mapping):
                raise TcaClientError("coordination task is missing claim record")
            claim = claim_record.get("claim")
            if not isinstance(claim, Mapping):
                raise TcaClientError("coordination task claim record is missing claim")
            claim_id = str(claim["claim_id"])
            result = await call_tool(
                mcp_url=mcp_url,
                token=token,
                name="submit_coordination_decision",
                arguments={
                    "project_id": project_id,
                    "claim_id": claim_id,
                    **decision,
                },
            )
            event = {
                "event": "decided",
                "project_id": project_id,
                "claim_id": claim_id,
                "decision": decision,
                "result": result,
            }
            processed.append(event)
            print_json(event)
        return {"processed": processed}
    if args.command == "approve":
        return await call_tool(
            mcp_url=mcp_url,
            token=token,
            name="submit_coordination_decision",
            arguments={
                "project_id": project_id,
                "claim_id": args.claim_id,
                "state": args.state,
                "reason": args.reason,
                "constraints": args.constraint,
                "wait_for": args.wait_for,
                "conflicts_with": args.conflicts_with,
            },
        )
    if args.command == "state":
        return await call_tool(
            mcp_url=mcp_url,
            token=token,
            name="get_claim_state",
            arguments={"project_id": project_id, "claim_id": args.claim_id},
        )
    if args.command == "wait":
        return await call_tool(
            mcp_url=mcp_url,
            token=token,
            name="wait_for_decision",
            arguments={
                "project_id": project_id,
                "claim_id": args.claim_id,
                "timeout_seconds": args.timeout,
                "poll_seconds": 0.25,
            },
        )
    if args.command == "start":
        return await call_tool(
            mcp_url=mcp_url,
            token=token,
            name="start_execution",
            arguments={"project_id": project_id, "claim_id": args.claim_id},
        )
    if args.command == "complete":
        return await call_tool(
            mcp_url=mcp_url,
            token=token,
            name="complete_claim",
            arguments={"project_id": project_id, "claim_id": args.claim_id},
        )
    raise AssertionError(args.command)


def main() -> None:
    args = build_parser().parse_args()
    try:
        print_json(asyncio.run(run(args)))
    except TcaClientError as exc:
        raise SystemExit(f"error: {exc}") from exc


if __name__ == "__main__":
    main()
