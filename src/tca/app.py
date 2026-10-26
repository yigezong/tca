from __future__ import annotations

import contextlib
import asyncio
import json
import os
from collections.abc import AsyncIterator
from pathlib import Path

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from .controller import Controller
from .github import GitHubClient, GitHubWebhookHandler, verify_github_signature
from .mcp_server import create_mcp
from .rules import BasicRuleChecker
from .store import build_store
from .project_access import ProjectAccess, current_principal


class BearerAuthMiddleware:
    def __init__(self, app, token: str, access: ProjectAccess | None = None) -> None:
        self.app = app
        self.token = token
        self.access = access

    async def __call__(self, scope, receive, send) -> None:
        protected = ("/mcp", "/api/")
        is_protected = scope["type"] == "http" and scope["path"].startswith(protected)
        if is_protected:
            headers = {key.lower(): value for key, value in scope.get("headers", [])}
            authorization = headers.get(b"authorization", b"")
            if self.access:
                try:
                    if not authorization.startswith(b"Bearer "):
                        raise PermissionError("missing GitHub token")
                    principal = await asyncio.to_thread(
                        self.access.github.principal, authorization[7:].decode("ascii")
                    )
                except (PermissionError, UnicodeDecodeError):
                    response = JSONResponse({"error": "unauthorized"}, status_code=401)
                    await response(scope, receive, send)
                    return
                context_token = current_principal.set(principal)
                try:
                    await self.app(scope, receive, send)
                finally:
                    current_principal.reset(context_token)
                return
            expected = f"Bearer {self.token}".encode()
            if authorization != expected:
                response = JSONResponse(
                    {"error": "unauthorized"},
                    status_code=401,
                    headers={"WWW-Authenticate": "Bearer"},
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


def build_components() -> tuple[Controller, GitHubWebhookHandler | None]:
    controller = Controller(
        build_store(),
        BasicRuleChecker(),
        execution_timeout=_float_env("TCA_EXECUTION_TIMEOUT_SECONDS", 3600.0),
        heartbeat_timeout=_float_env("TCA_HEARTBEAT_TIMEOUT_SECONDS", 900.0),
        m_decision_timeout=_float_env("TCA_M_DECISION_TIMEOUT_SECONDS", 600.0),
        allow_needs_human=not _bool_env("TCA_DISABLE_NEEDS_HUMAN", False),
    )

    github = None
    github_secret = os.getenv("GITHUB_WEBHOOK_SECRET", "")
    if github_secret:
        github_token = os.getenv("GITHUB_TOKEN", "")
        github_client = GitHubClient(token=github_token)
        github = GitHubWebhookHandler(
            controller=controller,
            github=github_client,
            secret=github_secret,
        )

    return controller, github


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw not in {"0", "false", "no", "off"}


def create_app():
    auth_mode = os.getenv("TCA_AUTH_MODE", "shared").strip().lower()
    if auth_mode not in {"shared", "github"}:
        raise RuntimeError("TCA_AUTH_MODE must be shared or github")
    token = os.getenv("TCA_API_TOKEN", "")
    if auth_mode == "shared" and not token:
        raise RuntimeError("TCA_API_TOKEN is required")

    controller, github = build_components()
    access = ProjectAccess(controller.store) if auth_mode == "github" else None
    mcp = create_mcp(controller, access)
    web_dir = Path(__file__).parent / "web"

    async def homepage(request: Request) -> HTMLResponse:
        return HTMLResponse((web_dir / "index.html").read_text(encoding="utf-8"))

    async def health(request: Request) -> JSONResponse:
        backend = "postgres" if os.getenv("DATABASE_URL") else "sqlite"
        return JSONResponse({"status": "ok", "store": backend})

    async def get_claim(request: Request) -> JSONResponse:
        project_id = request.query_params.get("project_id", "")
        if not project_id:
            return JSONResponse({"error": "project_id is required"}, status_code=400)
        try:
            if access:
                await asyncio.to_thread(access.role, project_id)
            return JSONResponse(
                controller.get_claim_state(project_id, request.path_params["claim_id"])
            )
        except PermissionError as exc:
            return JSONResponse({"error": str(exc)}, status_code=403)
        except KeyError:
            return JSONResponse({"error": "claim not found"}, status_code=404)

    async def projects(request: Request) -> JSONResponse:
        if access is None:
            return JSONResponse({"error": "GitHub project mode is disabled"}, status_code=404)
        try:
            if request.method == "GET":
                return JSONResponse({"projects": await asyncio.to_thread(access.list_projects)})
            data = await request.json()
            if not isinstance(data, dict):
                raise ValueError("project request must be a JSON object")
            started = await asyncio.to_thread(access.start, str(data.get("project_id", "")))
            return JSONResponse(started, status_code=201)
        except (PermissionError, ValueError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=403)

    async def join_project(request: Request) -> JSONResponse:
        if access is None:
            return JSONResponse({"error": "GitHub project mode is disabled"}, status_code=404)
        project_id = request.path_params["owner"] + "/" + request.path_params["repo"]
        try:
            return JSONResponse(await asyncio.to_thread(access.join, project_id))
        except PermissionError as exc:
            return JSONResponse({"error": str(exc)}, status_code=403)

    async def github_webhook(request: Request) -> JSONResponse:
        if github is None:
            return JSONResponse({"error": "GitHub webhook is disabled"}, status_code=404)
        try:
            body = await request.body()
            signature = request.headers.get("x-hub-signature-256")
            if access is not None:
                if not verify_github_signature(github.secret, body, signature):
                    raise PermissionError("invalid GitHub webhook signature")
                payload = json.loads(body)
                repository = str(payload.get("repository", {}).get("full_name", "")).lower()
                if access.store.project_owner(repository) is None:
                    raise PermissionError("repository has no TCA project")
                if str(payload.get("ref", "")).startswith("refs/heads/tca/claim/"):
                    raise PermissionError("GitHub-auth projects submit Claims through MCP")
            result = github.handle(
                body,
                signature,
                request.headers.get("x-github-event"),
            )
            return JSONResponse(
                {
                    "accepted": result.accepted,
                    "claim": result.claim.to_mapping() if result.claim else None,
                    "integrated_claims": [
                        record.to_mapping() for record in result.integrated_claims
                    ],
                },
                status_code=202,
            )
        except PermissionError as error:
            return JSONResponse({"error": str(error)}, status_code=401)
        except ValueError as error:
            return JSONResponse({"error": str(error)}, status_code=400)

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        async with mcp.session_manager.run():
            yield

    app = Starlette(
        routes=[
            Route("/", homepage),
            Mount("/static", app=StaticFiles(directory=web_dir)),
            Route("/health", health),
            Route("/api/claims/{claim_id}", get_claim),
            Route("/api/projects", projects, methods=["GET", "POST"]),
            Route("/api/projects/{owner}/{repo}/join", join_project, methods=["POST"]),
            Route("/webhooks/github", github_webhook, methods=["POST"]),
            Mount("/", app=mcp.streamable_http_app()),
        ],
        lifespan=lifespan,
    )
    return BearerAuthMiddleware(app, token, access)


def main() -> None:
    uvicorn.run(
        create_app(),
        host="0.0.0.0",
        port=8000,
    )


if __name__ == "__main__":
    main()
