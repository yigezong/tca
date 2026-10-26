"""Run the existing TCA controller as a local Codex MCP server."""

from __future__ import annotations

import argparse
from pathlib import Path

from .controller import Controller
from .mcp_server import create_mcp
from .rules import BasicRuleChecker
from .store import ClaimStore


def main() -> None:
    parser = argparse.ArgumentParser(description="Local TCA MCP server for Codex")
    parser.add_argument("--database", type=Path, required=True)
    args = parser.parse_args()
    database = args.database.expanduser().resolve()
    database.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    database.parent.chmod(0o700)
    store = ClaimStore(database)
    database.chmod(0o600)
    create_mcp(Controller(store, BasicRuleChecker())).run(
        transport="stdio"
    )


if __name__ == "__main__":
    main()
