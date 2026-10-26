import asyncio

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

import pytest

from tca.app import BearerAuthMiddleware, create_app


def make_app(token: str = "secret"):
    async def ok(request):
        return JSONResponse({"ok": True})

    app = Starlette(
        routes=[
            Route("/health", ok),
            Route("/api/claims/example", ok),
            Route("/mcp", ok, methods=["POST"]),
            Route("/webhooks/github", ok, methods=["POST"]),
        ]
    )
    return BearerAuthMiddleware(app, token)


def request_status(
    app,
    path: str,
    *,
    method: str = "GET",
    token: str = "",
    query_string: bytes = b"",
) -> int:
    async def call() -> int:
        messages = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            messages.append(message)

        headers = []
        if token:
            headers.append((b"authorization", f"Bearer {token}".encode()))
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": query_string,
            "headers": headers,
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
            "root_path": "",
        }
        await app(scope, receive, send)
        return next(
            message["status"]
            for message in messages
            if message["type"] == "http.response.start"
        )

    return asyncio.run(call())


def test_public_deployment_protects_mcp_and_claim_api() -> None:
    app = make_app()

    assert request_status(app, "/health") == 200
    assert request_status(app, "/webhooks/github", method="POST") == 200
    assert request_status(app, "/api/claims/example") == 401
    assert request_status(app, "/mcp", method="POST") == 401

    assert request_status(app, "/api/claims/example", token="secret") == 200
    assert request_status(app, "/mcp", method="POST", token="secret") == 200


def test_app_requires_api_token(monkeypatch) -> None:
    monkeypatch.delenv("TCA_API_TOKEN", raising=False)

    with pytest.raises(RuntimeError, match="TCA_API_TOKEN"):
        create_app()


def test_claim_api_requires_project_id(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TCA_API_TOKEN", "secret")
    monkeypatch.delenv("DATABASE_URL", raising=False)

    app = create_app()

    assert request_status(app, "/") == 200
    assert request_status(app, "/static/style.css") == 200
    assert request_status(app, "/api/claims/missing") == 401
    assert request_status(app, "/api/claims/missing", token="secret") == 400
    assert (
        request_status(
            app,
            "/api/claims/missing",
            token="secret",
            query_string=b"project_id=team%2Fproject",
        )
        == 404
    )
