#!/bin/sh
# Thin POSIX shim: locate a Python 3 and hand off to the cross-platform
# provisioner, hooks/bootstrap.py (the real logic lives there so macOS, Linux,
# WSL, and Git Bash share one implementation). Used by the skill's Step 0 defensive
# provisioning; the SessionStart hook calls bootstrap.py directly via the exec form.
set -eu

DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

for PY in python3 python; do
  if command -v "$PY" >/dev/null 2>&1; then
    exec "$PY" "$DIR/bootstrap.py" "$@"
  fi
done

echo "iFixAi: no python3/python on PATH — the engine needs Python 3.10+." >&2
exit 1
