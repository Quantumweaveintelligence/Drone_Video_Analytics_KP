#!/usr/bin/env bash
# check_zero_byte_py.sh — fail if any *.py except __init__.py is empty (a merge
# once emptied crowd/main.py — RunPod plan F1). Used by CI and the pre-commit hook.
set -euo pipefail
cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
empty="$(find . -name '*.py' ! -name '__init__.py' -size 0 \
          -not -path '*/node_modules/*' -not -path './.git/*' -not -path '*/.venv/*' -not -path '*/venv/*')"
if [ -n "$empty" ]; then
  echo "ERROR: zero-byte Python files (merge damage?):"; echo "$empty"; exit 1
fi
echo "zero-byte check: OK"
