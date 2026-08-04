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
from .github import GitHubContentLoader, GitHubWebhookHandler
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
    )
    branch_prefix = os.getenv("TCA_CLAIM_BRANCH_PREFIX", "tca/claim/")

    github = None
    github_secret = os.getenv("GITHUB_WEBHOOK_SECRET", "")
    if github_secret:
        github_token = os.getenv("GITHUB_TOKEN", "")
        api_url = os.getenv("GITHUB_API_URL", "https://api.github.com")
        loader = GitHubContentLoader(
            token=github_token,
            claim_path=os.getenv("TCA_CLAIM_PATH", ".tca/claim.yaml"),
            api_url=api_url,
        )
        github = GitHubWebhookHandler(
            controller=controller,
            loader=loader,
            secret=github_secret,
            branch_prefix=branch_prefix,
        )

    return controller, github


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
        try:
            return JSONResponse(controller.get_claim_state(request.path_params["claim_id"]))
        except KeyError:
            return JSONResponse({"error": "claim not found"}, status_code=404)

    async def github_webhook(request: Request) -> JSONResponse:
        if github is None:
            return JSONResponse({"error": "GitHub webhook is disabled"}, status_code=404)
        try:
            record = github.handle(
                await request.body(),
                request.headers.get("x-hub-signature-256"),
                request.headers.get("x-github-event"),
            )
            return JSONResponse(
                {"accepted": record is not None, "claim": record.to_mapping() if record else None},
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
