from __future__ import annotations

import contextlib
import os
from collections.abc import AsyncIterator
from pathlib import Path

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from .controller import Controller
from .coordinator import RuleBasedCoordinator
from .github import GitHubContentLoader, GitHubDecisionPublisher, GitHubWebhookHandler
from .mcp_server import create_mcp
from .store import ClaimStore


class BearerAuthMiddleware:
    def __init__(self, app, token: str) -> None:
        self.app = app
        self.token = token

    async def __call__(self, scope, receive, send) -> None:
        if self.token and scope["type"] == "http" and scope["path"].startswith("/mcp"):
            headers = {key.lower(): value for key, value in scope.get("headers", [])}
            expected = f"Bearer {self.token}".encode()
            if headers.get(b"authorization") != expected:
                response = JSONResponse({"error": "unauthorized"}, status_code=401)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


def build_components() -> tuple[Controller, GitHubWebhookHandler]:
    database = Path(os.getenv("TCA_DATABASE", "tca.db"))
    controller = Controller(ClaimStore(database), RuleBasedCoordinator())
    github_token = os.getenv("GITHUB_TOKEN", "")
    api_url = os.getenv("GITHUB_API_URL", "https://api.github.com")
    loader = GitHubContentLoader(
        token=github_token,
        claim_path=os.getenv("TCA_CLAIM_PATH", ".tca/claim.yaml"),
        api_url=api_url,
    )
    publisher = (
        GitHubDecisionPublisher(
            token=github_token,
            decision_path=os.getenv("TCA_DECISION_PATH", ".tca/decision.yaml"),
            api_url=api_url,
        )
        if github_token
        else None
    )
    github = GitHubWebhookHandler(
        controller=controller,
        loader=loader,
        secret=os.environ["GITHUB_WEBHOOK_SECRET"],
        publisher=publisher,
        branch_prefix=os.getenv("TCA_CLAIM_BRANCH_PREFIX", "tca/claim/"),
    )
    return controller, github


def create_app() -> Starlette:
    controller, github = build_components()
    mcp = create_mcp(controller)

    async def health(request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    async def get_claim(request: Request) -> JSONResponse:
        try:
            return JSONResponse(controller.get_claim_state(request.path_params["claim_id"]))
        except KeyError:
            return JSONResponse({"error": "claim not found"}, status_code=404)

    async def github_webhook(request: Request) -> JSONResponse:
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
    return BearerAuthMiddleware(app, os.getenv("TCA_API_TOKEN", ""))


def main() -> None:
    uvicorn.run(
        create_app(),
        host=os.getenv("TCA_HOST", "0.0.0.0"),
        port=int(os.getenv("TCA_PORT", "8000")),
    )


if __name__ == "__main__":
    main()
