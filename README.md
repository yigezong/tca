# Team Code Agent

Git-first coordination service for multiple coding agents working in one repository.

## V1 capabilities

- GitHub Claim-branch webhook ingestion with HMAC verification;
- deterministic Controller and SQLite state machine;
- replaceable coordinator interface with a path-conflict baseline;
- decision writeback to `.tca/decision.yaml`;
- remote MCP Server over Streamable HTTP;
- MCP tools for decision waiting, state, execution, heartbeat, progress, and completion;
- one ASGI process serving MCP, GitHub webhook, Claim API, and health checks;
- Docker image and uv-locked local environment.

## Local development

```bash
uv sync
uv run pytest
cp .env.example .env
```

Start the combined service:

```bash
set -a
source .env
set +a
uv run tca-app
```

Endpoints:

- `POST /mcp` — MCP Streamable HTTP;
- `POST /webhooks/github` — signed GitHub push webhook;
- `GET /api/claims/{claim_id}` — authoritative Claim state;
- `GET /health` — service health.

## Docker

```bash
docker build -t team-code-agent .
docker run --rm -p 8000:8000 --env-file .env -v tca-data:/data team-code-agent
```

## Claim branch

Push `.tca/claim.yaml` on a branch named `tca/claim/<claim-id>`. GitHub sends the push webhook to TCA. C validates and stores the Claim, invokes the coordinator, and writes `.tca/decision.yaml` back to the same branch when `GITHUB_TOKEN` is configured.

See [the project idea](docs/ideas/project-idea.md) for the product model.
