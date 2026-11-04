"""Install one restartable, user-scoped M process per worktree on macOS."""

from __future__ import annotations

import hashlib
import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

from .codex_setup import SetupError, tca_home


def label(repo: Path) -> str:
    digest = hashlib.sha256(str(repo.resolve()).encode()).hexdigest()[:16]
    return f"org.tca.manager.{digest}"


def paths(repo: Path) -> tuple[Path, Path]:
    name = label(repo)
    return Path.home() / "Library" / "LaunchAgents" / f"{name}.plist", tca_home() / "logs" / f"{name}.log"


def target(repo: Path) -> str:
    return f"gui/{os.getuid()}/{label(repo)}"


def running(repo: Path) -> bool:
    return subprocess.run(["launchctl", "print", target(repo)],
                          capture_output=True, text=True, check=False).returncode == 0


def start(repo: Path) -> None:
    if sys.platform != "darwin":
        raise SetupError("resident M currently requires macOS launchd")
    codex = shutil.which("codex")
    if codex is None:
        raise SetupError("Codex CLI is not on PATH")
    repo = repo.resolve()
    plist_path, log_path = paths(repo)
    plist_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    log_path.parent.chmod(0o700)
    descriptor = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    os.fchmod(descriptor, 0o600)
    os.close(descriptor)
    data = {
        "Label": label(repo),
        "ProgramArguments": [sys.executable, "-m", "tca.manager_worker",
                             "--repo", str(repo), "--codex-bin", codex],
        "WorkingDirectory": str(repo),
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": str(log_path),
        "StandardErrorPath": str(log_path),
        "EnvironmentVariables": {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin"),
            "TCA_HOME": str(tca_home()),
        },
    }
    content = plistlib.dumps(data)
    if running(repo):
        if plist_path.exists() and plist_path.read_bytes() == content:
            restarted = subprocess.run(["launchctl", "kickstart", "-k", target(repo)],
                                      check=False, capture_output=True, text=True)
            if restarted.returncode:
                raise SetupError(f"could not restart M: {restarted.stderr.strip()}")
            return
        stop(repo)
    plist_path.write_bytes(content)
    result = subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}",
                             str(plist_path)], text=True, capture_output=True, check=False)
    if result.returncode:
        raise SetupError(f"could not start M: {result.stderr.strip()}")


def stop(repo: Path) -> None:
    plist_path, _ = paths(repo)
    if running(repo):
        result = subprocess.run(["launchctl", "bootout", target(repo)],
                                text=True, capture_output=True, check=False)
        if result.returncode:
            raise SetupError(f"could not stop M: {result.stderr.strip()}")
    plist_path.unlink(missing_ok=True)
