"""Project-local MCP and turn-boundary adapters for non-Codex developers."""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

import yaml


def _write_owned(path: Path, content: str) -> None:
    if path.exists() and path.read_text(encoding="utf-8") != content:
        raise ValueError(f"Refusing to overwrite {path}; inspect it before setup")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _mcp_config(transport: dict) -> dict:
    if transport["type"] == "stdio":
        return {"type": "local", "command": [transport["command"], *transport["args"]]}
    return {"type": "remote", "url": transport["url"], "oauth": False,
            "headers": {"Authorization": "Bearer {env:" + transport["bearer_token_env_var"] + "}"}}


def _opencode_major() -> int:
    try:
        result = subprocess.run(["opencode", "--version"], text=True,
                                capture_output=True, check=False, timeout=10)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return 2
    match = re.search(r"\b(\d+)\.\d+\.\d+", result.stdout)
    return int(match.group(1)) if match else 2


def install_pi(repo: Path, name: str, transport: dict) -> None:
    config = repo / ".pi" / "mcp.json"
    current = json.loads(config.read_text(encoding="utf-8")) if config.exists() else {}
    if not isinstance(current, dict) or not isinstance(current.get("mcpServers", {}), dict):
        raise ValueError(f"Invalid Pi MCP configuration in {config}")
    entry = (_mcp_config(transport) if transport["type"] != "stdio" else
             {"command": transport["command"], "args": transport["args"]})
    if transport["type"] != "stdio":
        entry = {"url": transport["url"], "headers": entry["headers"]}
    old = current.get("mcpServers", {}).get(name)
    if old is not None and old != entry:
        raise ValueError(f"Pi MCP server '{name}' already differs in {config}")
    current.setdefault("mcpServers", {})[name] = entry
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
    extension = '''// TCA publishes bounded Git metadata when Pi becomes idle after a run.
export default function (pi) {
  pi.on("agent_settled", async (event, ctx) => {
    if (event.aborted) return;
    const turn = `${ctx.sessionManager.getSessionId()}:${ctx.sessionManager.getLeafId()}`;
    const result = await pi.exec(PYTHON, ["-m", "tca.capture_cli", "--repo", ctx.cwd,
      "--turn-id", turn], { cwd: ctx.cwd, timeout: 30000 });
    if (result.code !== 0) ctx.ui.notify("TCA worktree capture failed; check local TCA setup", "warning");
  });
}
'''.replace("PYTHON", json.dumps(sys.executable))
    _write_owned(repo / ".pi" / "extensions" / "tca.ts", extension)


def install_opencode(repo: Path, name: str, transport: dict) -> None:
    config = repo / ".opencode" / "opencode.json"
    current = json.loads(config.read_text(encoding="utf-8")) if config.exists() else {}
    if not isinstance(current, dict) or not isinstance(current.get("mcp", {}), dict):
        raise ValueError(f"Invalid OpenCode configuration in {config}")
    mcp = current.setdefault("mcp", {})
    if not isinstance(mcp.get("servers", {}), dict):
        raise ValueError(f"Invalid OpenCode MCP servers in {config}")
    entry = _mcp_config(transport)
    old = mcp.setdefault("servers", {}).get(name)
    if old is not None and old != entry:
        raise ValueError(f"OpenCode MCP server '{name}' already differs in {config}")
    mcp["servers"][name] = entry
    v1 = _opencode_major() < 2
    plugin_key = "plugin" if v1 else "plugins"
    plugin_path = "./plugin/tca.ts" if v1 else "./plugins/tca"
    plugins = current.setdefault(plugin_key, [])
    if not isinstance(plugins, list):
        raise ValueError(f"Invalid OpenCode plugins in {config}")
    if plugin_path not in plugins:
        plugins.append(plugin_path)
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
    plugin = '''import { Plugin } from "@opencode/plugin";
import { execFile } from "node:child_process";

export default Plugin.define({
  id: "tca.turn-capture",
  setup(ctx) {
    const controller = new AbortController();
    void (async () => {
      try {
        for await (const event of ctx.event.subscribe({
          types: ["session.execution.succeeded"], signal: controller.signal,
        })) {
          if (event.location?.directory !== ctx.location.directory) continue;
          execFile(PYTHON, ["-m", "tca.capture_cli", "--repo", ctx.location.directory,
            "--turn-id", event.id], { cwd: ctx.location.directory, timeout: 30000 }, (error) => {
            if (error) console.warn("TCA worktree capture failed; check local TCA setup");
          });
        }
      } catch (error) {
        if (!controller.signal.aborted) console.warn("TCA event subscription ended", error);
      }
    })();
    return () => controller.abort();
  },
});
'''.replace("PYTHON", json.dumps(sys.executable))
    if v1:
        plugin = '''import { execFile } from "node:child_process";

export const TCA = async ({ directory }) => {
  let turn = 0;
  return {
    event: async ({ event }) => {
      if (event.type !== "session.idle") return;
      const session = event.properties?.sessionID;
      if (!session) return;
      execFile(PYTHON, ["-m", "tca.capture_cli", "--repo", directory,
        "--turn-id", `${session}:${++turn}`], { cwd: directory, timeout: 30000 }, (error) => {
          if (error) console.warn("TCA worktree capture failed; check local TCA setup");
        });
    },
  };
};
'''.replace("PYTHON", json.dumps(sys.executable))
    path = (repo / ".opencode" / "plugin" / "tca.ts" if v1 else
            repo / ".opencode" / "plugins" / "tca" / "index.ts")
    _write_owned(path, plugin)


def install_dsh(repo: Path, name: str, transport: dict) -> None:
    command = (shlex.quote(sys.executable) + " -m tca.capture_cli --repo "
               + shlex.quote(str(repo)))
    hook = {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": command,
                                               "timeout": 30}]}]}}
    hook_path = repo / ".tca" / "dsh-hooks.json"
    _write_owned(hook_path, json.dumps(hook, indent=2) + "\n")
    mcp = {"serverName": name, "transport": "stdio" if transport["type"] == "stdio"
           else "streamable-http"}
    if transport["type"] == "stdio":
        mcp.update({"command": transport["command"], "args": transport["args"]})
    else:
        mcp["url"] = transport["url"]
        mcp["headers"] = {"Authorization": "!!js '`Bearer ${process.env."
                          + transport["bearer_token_env_var"] + "}`'"}
    # DSH patch lists append plugins to the selected profile; no profile is modified.
    patch = [
        {"insert": [{"id": "mcp-" + name, "name": "@deepseek-ai/dsh-mcp-client", "config": mcp}]},
        {"insert": [{"id": "tca-turn-hook", "name": "@deepseek-ai/dsh-hooks-claude-code",
                     "config": {"configPath": str(hook_path), "projectDir": str(repo)}}]},
    ]
    rendered = yaml.safe_dump(patch, sort_keys=False, allow_unicode=True)
    # PyYAML quotes the expression; DSH requires a YAML tag evaluated at boot.
    if transport["type"] != "stdio":
        expression = mcp["headers"]["Authorization"]
        rendered = rendered.replace("'" + expression.replace("'", "''") + "'", expression)
    _write_owned(repo / ".tca" / "dsh.patch.yml", rendered)


def install_adapter(client: str, repo: Path, name: str, transport: dict) -> None:
    try:
        {"pi": install_pi, "opencode": install_opencode, "dsh": install_dsh}[client](
            repo, name, transport)
    except ValueError as exc:
        from .codex_setup import SetupError
        raise SetupError(str(exc)) from exc
