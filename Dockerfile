FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH" \
    TCA_HOST="0.0.0.0" \
    TCA_PORT="8000" \
    TCA_DATABASE="/data/tca.db"

VOLUME ["/data"]
EXPOSE 8000
CMD ["tca-app"]
