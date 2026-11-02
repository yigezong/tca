"""Client-side Git checks and server-side validation of reported delivery."""

from __future__ import annotations

import ast
import fnmatch
import os
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Mapping


def path_in_scope(path: str, planned_paths: tuple[str, ...]) -> bool:
    if path.startswith("/") or ".." in Path(path).parts or not path:
        return False
    return any(
        fnmatch.fnmatchcase(path, pattern)
        or path == pattern.rstrip("/")
        or path.startswith(pattern.rstrip("/") + "/")
        for pattern in planned_paths
    )


def validate_completion(claim: object, evidence: Mapping[str, object]) -> None:
    commit = evidence.get("commit_sha")
    base = evidence.get("base_commit")
    changed = evidence.get("changed_paths")
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40,64}", commit):
        raise ValueError("completion requires a full Git commit SHA")
    if base != getattr(claim, "base_commit"):
        raise ValueError("completion base_commit differs from the Claim")
    if (
        not isinstance(changed, list)
        or not changed
        or not all(isinstance(p, str) for p in changed)
    ):
        raise ValueError("completion requires nonempty changed_paths")
    outside = [p for p in changed if not path_in_scope(p, getattr(claim, "planned_paths"))]
    if outside:
        raise ValueError("changed paths outside Claim scope: " + ", ".join(outside))
    symbols = evidence.get("changed_symbols", [])
    if not isinstance(symbols, list) or not all(
        isinstance(item, str) for item in symbols
    ):
        raise ValueError("changed_symbols must be a list of strings")
    owned = set(getattr(claim, "owned_symbols", ()))
    if owned:
        outside_symbols = []
        for item in symbols:
            symbol_path, _, name = item.partition("::")
            if not name or not any(
                item == allowance
                or name == allowance
                or name.startswith(allowance + ".")
                for allowance in owned
            ) or symbol_path not in changed:
                outside_symbols.append(item)
        if outside_symbols:
            raise ValueError(
                "changed Python symbols outside Claim ownership: "
                + ", ".join(outside_symbols)
            )


def python_symbols(source: str, filename: str) -> dict[str, str]:
    tree = ast.parse(source, filename=filename)
    found: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found[node.name] = ast.dump(node, include_attributes=False)
        elif isinstance(node, ast.ClassDef):
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    found[f"{node.name}.{child.name}"] = ast.dump(
                        child, include_attributes=False
                    )
    return found


def verify_local_git(
    repo: Path,
    claim: Mapping[str, object],
    *,
    require_push: bool = True,
) -> dict[str, object]:
    """Verify the checked-out branch and committed diff before reporting completion.

    This is an honest-client check; a metadata-only server cannot inspect a
    private repository's Git objects or independently verify symbol ownership.
    """
    def git(*args: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(repo), *args], text=True, capture_output=True,
            timeout=20, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
        if result.returncode:
            raise ValueError(result.stderr.strip() or f"git {' '.join(args)} failed")
        return result.stdout.strip()

    branch = str(claim["work_branch"])
    base = str(claim["base_commit"])
    if git("branch", "--show-current") != branch:
        raise ValueError(f"check out the Claim work branch {branch} before completion")
    if git("status", "--porcelain"):
        raise ValueError("commit or discard uncommitted changes before completion")
    head = git("rev-parse", "HEAD")
    git("merge-base", "--is-ancestor", base, head)
    if head == git("rev-parse", base):
        raise ValueError("work branch has no new commit")
    if require_push:
        remote = git("ls-remote", "--heads", "origin", branch)
        if not remote or remote.split()[0] != head:
            raise ValueError("push the Claim work branch to origin before completion")
    changed = sorted(set(
        git("diff", "--no-renames", "--name-only", base, head).splitlines()
    ))
    changed_symbols: list[str] = []
    for path in changed:
        if (
            not path.endswith(".py")
            or path.startswith("tests/")
            or Path(path).name.startswith("test_")
        ):
            continue
        before = subprocess.run(
            ["git", "-C", str(repo), "show", f"{base}:{path}"],
            text=True, capture_output=True, timeout=20,
        )
        old = python_symbols(before.stdout if before.returncode == 0 else "", path)
        after_path = repo / path
        new = python_symbols(after_path.read_text() if after_path.exists() else "", path)
        changed_symbols.extend(
            f"{path}::{name}" for name in sorted(old.keys() | new.keys())
            if old.get(name) != new.get(name)
        )
    evidence = {
        "base_commit": base, "commit_sha": head,
        "changed_paths": changed, "changed_symbols": changed_symbols,
    }
    validate_completion(SimpleNamespace(
        base_commit=base, planned_paths=tuple(claim["planned_paths"]),
        owned_symbols=tuple(claim.get("owned_symbols", ())),
    ), evidence)
    return evidence
