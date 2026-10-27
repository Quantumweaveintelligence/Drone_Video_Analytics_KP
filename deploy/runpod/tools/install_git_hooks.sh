#!/usr/bin/env bash
# install_git_hooks.sh — Phase 1.2: pre-commit hook that blocks zero-byte *.py files.
set -euo pipefail
top="$(git rev-parse --show-toplevel)"
hook="$top/.git/hooks/pre-commit"
cat > "$hook" <<'HOOK'
#!/usr/bin/env bash
exec "$(git rev-parse --show-toplevel)/deploy/runpod/tools/check_zero_byte_py.sh"
HOOK
chmod +x "$hook"
echo "installed $hook"
