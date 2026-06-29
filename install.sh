#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python_cmd="${PYTHON:-python3}"

cd "$repo_dir"
if ! "$python_cmd" -m pip --version >/dev/null 2>&1; then
  "$python_cmd" -m ensurepip --upgrade
fi
"$python_cmd" -m pip install -e .
"$python_cmd" -m ultrastealth.install "$@"
