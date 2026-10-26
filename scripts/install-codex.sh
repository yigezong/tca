#!/bin/sh
set -eu

if [ "$#" -lt 1 ]; then
  echo "Usage: sh scripts/install-codex.sh /path/to/your-git-repo [tca-codex init options]" >&2
  exit 2
fi

target_repo=$1
shift
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
package_dir=$(dirname -- "$script_dir")

if ! command -v codex >/dev/null 2>&1; then
  echo "Codex CLI is required. Install Codex and make 'codex' available on PATH." >&2
  exit 2
fi
if ! command -v git >/dev/null 2>&1; then
  echo "Git is required for TCA worktrees and commits." >&2
  exit 2
fi

if command -v uv >/dev/null 2>&1; then
  uv_bin=$(command -v uv)
else
  if ! command -v curl >/dev/null 2>&1; then
    echo "curl or an existing uv installation is required for the first install." >&2
    exit 2
  fi
  echo "Installing uv, which supplies an isolated Python runtime for TCA..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  uv_bin="$HOME/.local/bin/uv"
  if [ ! -x "$uv_bin" ]; then
    echo "uv installed outside $HOME/.local/bin; add it to PATH and rerun." >&2
    exit 2
  fi
fi

"$uv_bin" tool install --force --refresh-package team-code-agent --python 3.12 "$package_dir"
tool_bin=$("$uv_bin" tool dir --bin)
"$tool_bin/tca-codex" init --repo "$target_repo" "$@"
"$tool_bin/tca-codex" doctor --repo "$target_repo"
