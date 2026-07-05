from __future__ import annotations

import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .controller import Controller
from .coordinator import RuleBasedCoordinator
from .github import GitHubContentLoader, GitHubDecisionPublisher, GitHubWebhookHandler
from .store import ClaimStore


class TCAApplication:
    def __init__(self, controller: Controller, github: GitHubWebhookHandler) -> None:
        self.controller = controller
        self.github = github

    def health(self) -> dict[str, str]:
        return {"status": "ok"}

    def claim(self, claim_id: str) -> dict[str, object]:
        return self.controller.get_claim_state(claim_id)

    def github_webhook(
        self, body: bytes, signature: str | None, event: str | None
    ) -> dict[str, object]:
        record = self.github.handle(body, signature, event)
        return {"accepted": record is not None, "claim": record.to_mapping() if record else None}


def make_handler(application: TCAApplication) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "TCA/0.1"

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/health":
                self._json(HTTPStatus.OK, application.health())
                return
            if self.path.startswith("/api/claims/"):
                claim_id = self.path.removeprefix("/api/claims/").split("?", 1)[0]
                try:
                    self._json(HTTPStatus.OK, application.claim(claim_id))
                except KeyError:
                    self._json(HTTPStatus.NOT_FOUND, {"error": "claim not found"})
                return
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/webhooks/github":
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                return
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            try:
                result = application.github_webhook(
                    body,
                    self.headers.get("X-Hub-Signature-256"),
                    self.headers.get("X-GitHub-Event"),
                )
                self._json(HTTPStatus.ACCEPTED, result)
            except PermissionError as error:
                self._json(HTTPStatus.UNAUTHORIZED, {"error": str(error)})
            except (ValueError, json.JSONDecodeError) as error:
                self._json(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            except Exception:
                self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "internal error"})

        def log_message(self, format: str, *args: Any) -> None:
            if os.getenv("TCA_HTTP_LOG", "1") == "1":
                super().log_message(format, *args)

        def _json(self, status: HTTPStatus, value: object) -> None:
            encoded = json.dumps(value, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    return Handler


def build_application() -> TCAApplication:
    database = Path(os.getenv("TCA_DATABASE", "tca.db"))
    store = ClaimStore(database)
    controller = Controller(store, RuleBasedCoordinator())
    loader = GitHubContentLoader(
        token=os.getenv("GITHUB_TOKEN", ""),
        claim_path=os.getenv("TCA_CLAIM_PATH", ".tca/claim.yaml"),
        api_url=os.getenv("GITHUB_API_URL", "https://api.github.com"),
    )
    github_token = os.getenv("GITHUB_TOKEN", "")
    publisher = (
        GitHubDecisionPublisher(
            token=github_token,
            decision_path=os.getenv("TCA_DECISION_PATH", ".tca/decision.yaml"),
            api_url=os.getenv("GITHUB_API_URL", "https://api.github.com"),
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
    return TCAApplication(controller, github)


def main() -> None:
    host = os.getenv("TCA_HOST", "127.0.0.1")
    port = int(os.getenv("TCA_PORT", "8080"))
    application = build_application()
    server = ThreadingHTTPServer((host, port), make_handler(application))
    print(f"TCA server listening on http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
