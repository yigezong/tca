"""Bounded metadata about uncommitted work; source stays in the A worktree."""

from __future__ import annotations

import ast
import fnmatch
import hashlib
import subprocess
from pathlib import Path


def _git(repo: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, check=False,
        timeout=20,
    )
    if result.returncode:
        raise ValueError(result.stderr.decode(errors="replace").strip() or "git failed")
    return result.stdout


def _annotation(node: ast.expr | None) -> str:
    if isinstance(node, ast.Name):
        return node.id[:40]
    if isinstance(node, ast.Attribute):
        return node.attr[:40]
    return "?" if node is not None else ""


def _shape(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    names = [arg.arg for arg in (*node.args.posonlyargs, *node.args.args)]
    if node.args.vararg:
        names.append("*" + node.args.vararg.arg)
    names.extend(arg.arg for arg in node.args.kwonlyargs)
    if node.args.kwarg:
        names.append("**" + node.args.kwarg.arg)
    result = f"({','.join(names)})"
    if node.returns is not None:
        result += "->" + _annotation(node.returns)
    return result[:120]


def _functions(source: bytes) -> dict[str, tuple[str, str]]:
    try:
        tree = ast.parse(source.decode("utf-8"))
    except (UnicodeDecodeError, SyntaxError):
        return {}
    result: dict[str, tuple[str, str]] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            result[node.name] = (_shape(node), ast.dump(node, include_attributes=False))
        elif isinstance(node, ast.ClassDef):
            for member in node.body:
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    result[f"{node.name}.{member.name}"] = (
                        _shape(member), ast.dump(member, include_attributes=False)
                    )
    return result


def build_worktree_digest(repo: Path, claim: dict[str, object]) -> dict[str, object]:
    """Compare HEAD with the worktree, sending names/shapes but no file text."""
    head = _git(repo, "rev-parse", "HEAD").decode().strip()
    tracked = _git(repo, "diff", "--name-only", "-z", "HEAD").decode().split("\0")
    untracked = _git(repo, "ls-files", "--others", "--exclude-standard", "-z").decode().split("\0")
    changed = sorted(set(path for path in (*tracked, *untracked) if path))
    patterns = tuple(str(path) for path in claim.get("planned_paths", ()))
    in_scope = [
        path for path in changed
        if any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)
    ]
    outside = len(changed) - len(in_scope)
    fingerprint = hashlib.sha256()
    fingerprint.update(head.encode())
    fingerprint.update(str(outside).encode())
    changed_symbols: list[str] = []
    api_changes: list[str] = []
    truncated = len(in_scope) > 40
    for path in in_scope[:40]:
        if len(path) > 160 or "\n" in path or "\r" in path:
            truncated = True
            continue
        local = repo / path
        try:
            content = local.read_bytes() if local.is_file() else b""
        except OSError:
            content = b""
        fingerprint.update(path.encode())
        fingerprint.update(hashlib.sha256(content).digest())
        if not path.endswith(".py") or len(content) > 262_144:
            if len(content) > 262_144:
                truncated = True
            continue
        previous = subprocess.run(
            ["git", "-C", str(repo), "show", f"HEAD:{path}"],
            capture_output=True, check=False, timeout=20,
        )
        old = _functions(previous.stdout if previous.returncode == 0 else b"")
        new = _functions(content)
        for symbol in sorted(set(old) | set(new)):
            before = old.get(symbol)
            after = new.get(symbol)
            label = f"{path}:{symbol}"[:160]
            if before is None:
                api_changes.append(f"{label}: added {after[0]}")
            elif after is None:
                api_changes.append(f"{label}: removed {before[0]}")
            elif before[0] != after[0]:
                api_changes.append(f"{label}: {before[0]} -> {after[0]}")
            elif before[1] != after[1]:
                changed_symbols.append(label)
    if len(changed_symbols) > 40 or len(api_changes) > 30:
        truncated = True
    return {
        "head": head,
        "fingerprint": fingerprint.hexdigest(),
        "changed_paths": [path for path in in_scope[:40] if len(path) <= 160 and "\n" not in path and "\r" not in path],
        "changed_symbols": changed_symbols[:40],
        "api_changes": [item[:200] for item in api_changes[:30]],
        "outside_scope_count": outside,
        "truncated": truncated,
    }
