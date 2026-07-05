from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

import pytest

from tca.app import BearerAuthMiddleware, create_app


def make_client(token: str = "secret") -> TestClient:
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
    return TestClient(BearerAuthMiddleware(app, token))


def test_public_deployment_protects_mcp_and_claim_api() -> None:
    client = make_client()

    assert client.get("/health").status_code == 200
    assert client.post("/webhooks/github").status_code == 200
    assert client.get("/api/claims/example").status_code == 401
    assert client.post("/mcp").status_code == 401

    headers = {"Authorization": "Bearer secret"}
    assert client.get("/api/claims/example", headers=headers).status_code == 200
    assert client.post("/mcp", headers=headers).status_code == 200


def test_vercel_requires_api_token(monkeypatch) -> None:
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.delenv("TCA_API_TOKEN", raising=False)

    with pytest.raises(RuntimeError, match="TCA_API_TOKEN"):
        create_app()
