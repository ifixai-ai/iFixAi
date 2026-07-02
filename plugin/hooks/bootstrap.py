#!/usr/bin/env python3
"""Provision the iFixAi engine into the plugin's PERSISTENT data dir so the skill
can run `ifixai run` with no cloned repo.

Cross-platform: a venv's console scripts live in `bin/` on macOS/Linux/WSL and in
`Scripts/` on native Windows (and gain a `.exe` suffix there), so this resolves
the layout from `os.name` instead of hard-coding `bin/`. The provisioning logic
lives here, in one place; the per-shell shims (`bootstrap.sh` for POSIX shells and
Git Bash, `bootstrap.ps1` for native Windows PowerShell) only locate a Python and
hand off to this file.

Idempotent: re-installs only when the pinned requirements change, so it is a no-op
after the first session. Runs from the SessionStart hook (exec form,
`python3 hooks/bootstrap.py`) AND defensively from the skill's Step 0, in case
hooks don't fire on a given surface.
"""

from __future__ import annotations

import filecmp
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import NoReturn


def _fail(msg: str) -> NoReturn:
    print(f"iFixAi: {msg}", file=sys.stderr)
    raise SystemExit(1)


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        _fail(f"{name} is not set. Your agent's plugin host sets it for an installed plugin.")
    return value


def _venv_bin(venv: Path) -> Path:
    """A venv's console-script dir: `Scripts` on Windows, otherwise `bin`."""
    return venv / ("Scripts" if os.name == "nt" else "bin")


def _exe(bin_dir: Path, name: str) -> Path:
    """An executable inside a venv bin dir, with the `.exe` suffix on Windows."""
    return bin_dir / (f"{name}.exe" if os.name == "nt" else name)


def main() -> None:
    # The engine requires 3.10+. The shims prefer a `python3`/`py -3`, but guard
    # here too in case the hook resolved an older interpreter.
    if sys.version_info < (3, 10):
        _fail(f"the engine needs Python 3.10+, but this is {sys.version.split()[0]}.")

    data = Path(_required_env("CLAUDE_PLUGIN_DATA"))
    root = Path(_required_env("CLAUDE_PLUGIN_ROOT"))
    req = root / "requirements.txt"
    venv = data / "venv"
    stamp = data / "requirements.installed.txt"
    bin_dir = _venv_bin(venv)
    engine = _exe(bin_dir, "ifixai")

    # Local/dev engine override, for testing the plugin BEFORE the pinned version
    # is on PyPI. Set IFIXAI_ENGINE_SPEC to a wheel path, a directory, or
    # "-e /path/to/repo" and the engine installs from there instead of
    # requirements.txt. When set we always (re)install — dev builds change without
    # the pin changing. Unset = the published pin (the real-user path).
    spec = os.environ.get("IFIXAI_ENGINE_SPEC", "").strip()

    # Already provisioned for these exact requirements? Nothing to do. (Skipped
    # under a dev override so a rebuilt wheel always reinstalls.)
    if (
        not spec
        and engine.exists()
        and req.exists()
        and stamp.exists()
        and filecmp.cmp(req, stamp, shallow=False)
    ):
        return

    print("iFixAi: provisioning the engine…", file=sys.stderr)
    data.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)

    pip = [str(_exe(bin_dir, "python")), "-m", "pip", "install", "--quiet"]
    subprocess.run([*pip, "--upgrade", "pip"], check=True)
    if spec:
        print(f"iFixAi: installing local engine spec: {spec}", file=sys.stderr)
        # posix=False keeps Windows backslash paths intact when splitting "-e C:\…".
        subprocess.run([*pip, *shlex.split(spec, posix=os.name != "nt")], check=True)
    else:
        subprocess.run([*pip, "-r", str(req)], check=True)
        # Copy bytes verbatim — write_text/read_text would translate newlines (CRLF
        # on Windows), so the byte-compare above would never match and the engine
        # would re-provision every session.
        stamp.write_bytes(req.read_bytes())

    print(f"iFixAi: engine ready in {venv} — run /ifixai:ifixai to start.", file=sys.stderr)


if __name__ == "__main__":
    main()
