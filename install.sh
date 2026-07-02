#!/usr/bin/env bash
set -euo pipefail

repo_url="${ULTRASTEALTH_REPO_URL:-https://github.com/anusoft/ultrastealth.git}"
repo_ref="${ULTRASTEALTH_REF:-main}"
install_dir="${ULTRASTEALTH_INSTALL_DIR:-$HOME/.ultrastealth/src/ultrastealth}"
python_cmd="${PYTHON:-python3}"

usage() {
  cat <<'USAGE'
Usage: install.sh [ultrastealth-install options]

Install Ultrastealth from a local clone, or via curl|bash:
  curl -fsSL https://raw.githubusercontent.com/anusoft/ultrastealth/main/install.sh | bash

Environment:
  PYTHON=/path/to/python3
  ULTRASTEALTH_INSTALL_DIR=~/.ultrastealth/src/ultrastealth
  ULTRASTEALTH_REPO_URL=https://github.com/anusoft/ultrastealth.git
  ULTRASTEALTH_REF=main
USAGE
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi

script_path="${BASH_SOURCE[0]:-$0}"
script_dir=""
if [[ -n "$script_path" && -f "$script_path" ]]; then
  script_dir="$(cd "$(dirname "$script_path")" && pwd)"
fi

if [[ -n "$script_dir" && -f "$script_dir/pyproject.toml" && -f "$script_dir/install.py" ]]; then
  repo_dir="$script_dir"
else
  repo_dir="$install_dir"
  mkdir -p "$(dirname "$repo_dir")"
  if command -v git >/dev/null 2>&1; then
    if [[ -d "$repo_dir/.git" ]]; then
      git -C "$repo_dir" fetch --depth 1 origin "$repo_ref"
      git -C "$repo_dir" checkout --quiet FETCH_HEAD
    else
      rm -rf "$repo_dir"
      git clone --depth 1 --branch "$repo_ref" "$repo_url" "$repo_dir"
    fi
  else
    if ! command -v curl >/dev/null 2>&1; then
      echo "install.sh needs git or curl to fetch Ultrastealth." >&2
      exit 1
    fi
    rm -rf "$repo_dir"
    mkdir -p "$repo_dir"
    curl -fsSL "https://codeload.github.com/anusoft/ultrastealth/tar.gz/${repo_ref}" \
      | tar -xz --strip-components=1 -C "$repo_dir"
  fi
fi

cd "$repo_dir"
if ! "$python_cmd" -m pip --version >/dev/null 2>&1; then
  "$python_cmd" -m ensurepip --upgrade
fi
"$python_cmd" -m pip install -e .
"$python_cmd" -m ultrastealth.install "$@"
