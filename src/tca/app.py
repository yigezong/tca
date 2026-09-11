from __future__ import annotations

import contextlib
import os
from collections.abc import AsyncIterator

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from .controller import Controller
from .github import GitHubClient, GitHubWebhookHandler
from .mcp_server import create_mcp
from .rules import BasicRuleChecker
from .store import build_store


class BearerAuthMiddleware:
    def __init__(self, app, token: str) -> None:
        self.app = app
        self.token = token

    async def __call__(self, scope, receive, send) -> None:
        protected = ("/mcp", "/api/")
        is_protected = (
            self.token
            and scope["type"] == "http"
            and scope["path"].startswith(protected)
        )
        if is_protected:
            headers = {key.lower(): value for key, value in scope.get("headers", [])}
            expected = f"Bearer {self.token}".encode()
            if headers.get(b"authorization") != expected:
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
    token = os.getenv("TCA_API_TOKEN", "")
    if not token:
        raise RuntimeError("TCA_API_TOKEN is required")

    controller, github = build_components()
    mcp = create_mcp(controller)

    async def health(request: Request) -> JSONResponse:
        backend = "postgres" if os.getenv("DATABASE_URL") else "sqlite"
        return JSONResponse({"status": "ok", "store": backend})

    async def get_claim(request: Request) -> JSONResponse:
        project_id = request.query_params.get("project_id", "")
        if not project_id:
            return JSONResponse({"error": "project_id is required"}, status_code=400)
        try:
            return JSONResponse(
                controller.get_claim_state(project_id, request.path_params["claim_id"])
            )
        except KeyError:
            return JSONResponse({"error": "claim not found"}, status_code=404)

    async def github_webhook(request: Request) -> JSONResponse:
        if github is None:
            return JSONResponse({"error": "GitHub webhook is disabled"}, status_code=404)
        try:
            result = github.handle(
                await request.body(),
                request.headers.get("x-hub-signature-256"),
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
            Route("/health", health),
            Route("/api/claims/{claim_id}", get_claim),
            Route("/webhooks/github", github_webhook, methods=["POST"]),
            Mount("/", app=mcp.streamable_http_app()),
        ],
        lifespan=lifespan,
    )
    return BearerAuthMiddleware(app, token)


def main() -> None:
    uvicorn.run(
        create_app(),
        host="0.0.0.0",
        port=8000,
    )


if __name__ == "__main__":
    main()
