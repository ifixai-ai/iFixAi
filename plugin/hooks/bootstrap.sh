#!/bin/sh
# Provision the iFixAi engine into the plugin's PERSISTENT data dir so the skill
# can run `ifixai-diagnose` with no cloned repo. Idempotent: re-installs only when
# the pinned requirements change, so it is a no-op after the first session.
#
# Runs from the SessionStart hook AND defensively from the skill's Step 0 (in case
# hooks don't fire on a given surface). Both call this same script.
set -eu

DATA="${CLAUDE_PLUGIN_DATA:?set by Claude Code for an installed plugin}"
ROOT="${CLAUDE_PLUGIN_ROOT:?set by Claude Code for an installed plugin}"
REQ="$ROOT/requirements.txt"
VENV="$DATA/venv"
STAMP="$DATA/requirements.installed.txt"

# Local/dev engine override, for testing the plugin BEFORE the pinned version is
# on PyPI. Set IFIXAI_ENGINE_SPEC to a wheel path, a directory, or "-e /path/to/repo"
# and the engine installs from there instead of requirements.txt. When set we always
# (re)install — dev builds change without the pin changing. Unset = the published
# pin (the real-user path).
SPEC="${IFIXAI_ENGINE_SPEC:-}"

# Already provisioned for these exact requirements? Nothing to do. (Skipped under a
# dev override so a rebuilt wheel always reinstalls.)
if [ -z "$SPEC" ] && [ -x "$VENV/bin/ifixai-diagnose" ] && cmp -s "$REQ" "$STAMP" 2>/dev/null; then
  exit 0
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "iFixAi: python3 not found on PATH — the engine needs Python 3.10+." >&2
  exit 1
fi

echo "iFixAi: provisioning the engine…" >&2
mkdir -p "$DATA"
python3 -m venv "$VENV"
"$VENV/bin/python" -m pip install --quiet --upgrade pip
if [ -n "$SPEC" ]; then
  echo "iFixAi: installing local engine spec: $SPEC" >&2
  # Intentionally unquoted so "-e /path" splits into two args.
  # shellcheck disable=SC2086
  "$VENV/bin/python" -m pip install --quiet $SPEC
else
  "$VENV/bin/python" -m pip install --quiet -r "$REQ"
  cp "$REQ" "$STAMP"
fi
echo "iFixAi: engine ready in $VENV — run /ifixai:ifixai to start." >&2
