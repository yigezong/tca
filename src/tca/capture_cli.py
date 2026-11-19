"""Small runtime-neutral entrypoint used by coding-agent turn hooks."""

from __future__ import annotations

import argparse
import subprocess
import sys
import uuid
from pathlib import Path

from .codex_setup import SetupError
from .turn_hook import capture_worktree


def main() -> None:
    parser = argparse.ArgumentParser(description="Capture bounded TCA worktree metadata")
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--turn-id", default="")
    args = parser.parse_args()
    try:
        capture_worktree(args.repo, args.turn_id or uuid.uuid4().hex)
    except (ValueError, OSError, SetupError, KeyError, subprocess.TimeoutExpired) as exc:
        print(f"TCA turn capture failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print("{}")


if __name__ == "__main__":
    main()
