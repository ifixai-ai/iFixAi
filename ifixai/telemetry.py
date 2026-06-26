"""Pseudonymous run telemetry for iFixAi.

We count how often iFixAi is run, by how many distinct installs, and whether the
same install returns across days. One ``ifixai_started`` event per run plus an
``ifixai_completed`` event on a finished run, keyed by a random per-install id
stored locally. Stdlib only: a synchronous ``urllib`` POST on a daemon thread,
deliberately not aiohttp (the run paths close their asyncio loop before exit, so
the existing session can't be reused at flush time).

Telemetry must never block, slow, or break a run: every path is wrapped, the send
is fire-and-forget with a hard timeout, and failures are swallowed. See the
Telemetry section of ``SECURITY.md`` for what's sent, the opt-outs, and the
privacy posture.
"""

from __future__ import annotations

import atexit
import json
import os
import platform
import queue
import ssl
import sys
import threading
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

from ifixai._version import VERSION

# PostHog (US region) capture endpoint + project key. The project key is public
# by design (write-only: it can send events, never read them), so it ships as a
# constant. Pinned literals, no env/flag override (an overridable URL would be an
# SSRF/redirect primitive).
POSTHOG_ENDPOINT = "https://us.i.posthog.com/i/v0/e/"
POSTHOG_PROJECT_KEY = "phc_sSAKNHJwLY748x2BtZvDPR5xdnMr7PndS2gmnb2LsD2H"

# The only user-data property keys that may ever leave the machine.
_ALLOWED_PROPS = frozenset({"version", "os", "surface"})

_REQUEST_TIMEOUT = 1.0  # seconds, hard cap per send
_FLUSH_BUDGET = 1.0  # seconds, total atexit join budget

# Env vars that indicate an automated CI run (telemetry off).
_CI_VENDOR_VARS = (
    "GITHUB_ACTIONS", "GITLAB_CI", "BUILDKITE", "CIRCLECI",
    "JENKINS_URL", "TF_BUILD", "TEAMCITY_VERSION", "TRAVIS", "APPVEYOR",
)

_DISCLOSURE_TEXT = (
    "iFixAi sends pseudonymous run telemetry: a random local install id (a "
    "persistent identifier, so it counts as personal data under GDPR), whether a "
    "run started and completed, the tool version, your OS name, whether you ran "
    "the CLI or the plugin, and a UTC timestamp. It never sends your code, "
    "findings, grades, prompts, file paths, "
    "or IP address — run with --print-telemetry to see exactly what goes out. "
    "Opt out anytime with --no-telemetry, IFIXAI_TELEMETRY=0, or DO_NOT_TRACK=1 "
    "(and it's off automatically in CI); opting out sends nothing."
)

_started_emitted = False
_completed_emitted = False
_force_disabled = False
_sender: "_Sender | None" = None
_sender_lock = threading.Lock()


# --------------------------------------------------------------------------- #
# Storage paths
# --------------------------------------------------------------------------- #
def _config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "ifixai"


def _id_path() -> Path:
    return _config_dir() / "install-id"


def _disclosure_path() -> Path:
    return _config_dir() / "disclosure-shown"


def _optout_path() -> Path:
    return _config_dir() / "telemetry-opt-out"


def _ensure_config_dir() -> Path:
    """Create the config dir (best-effort ``0700``) and return it."""
    directory = _config_dir()
    directory.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(directory, 0o700)
    except OSError:
        pass
    return directory


# --------------------------------------------------------------------------- #
# Opt-out resolution
# --------------------------------------------------------------------------- #
def disable() -> None:
    """Disable telemetry for this process (wired to the ``--no-telemetry`` flag)."""
    global _force_disabled
    _force_disabled = True


def _in_ci() -> bool:
    ci = os.environ.get("CI")
    if ci is not None and ci.strip().lower() not in {"", "0", "false", "no"}:
        return True
    return any(v in os.environ for v in _CI_VENDOR_VARS)


def is_enabled() -> bool:
    """Resolve every opt-out. Off if any matches; a filesystem error reads as off."""
    if _force_disabled:
        return False
    val = os.environ.get("IFIXAI_TELEMETRY")
    if val is not None and val.strip().lower() in {"0", "false", "no", "off"}:
        return False
    if "DO_NOT_TRACK" in os.environ:  # presence-based, any value incl. "0"
        return False
    if _in_ci():
        return False
    try:
        if _optout_path().exists():
            return False
    except OSError:
        return False
    return True


# --------------------------------------------------------------------------- #
# Install id
# --------------------------------------------------------------------------- #
def _read_id() -> str | None:
    try:
        value = _id_path().read_text(encoding="utf-8").strip()
        return value or None
    except OSError:
        return None


def install_id() -> str | None:
    """Return the persistent install id, creating it once. ``None`` on fs failure.

    Creation is an exclusive create (``O_EXCL``): if two fresh runs race, only the
    first writer makes the file and everyone re-reads the same winning id.
    """
    existing = _read_id()
    if existing is not None:
        return existing
    path = _id_path()
    try:
        _ensure_config_dir()
        new_id = str(uuid.uuid4())
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            try:
                os.write(fd, new_id.encode("utf-8"))
            finally:
                os.close(fd)
        except FileExistsError:
            pass  # another process won the race; read its id below
        return _read_id()
    except OSError:
        return None


# --------------------------------------------------------------------------- #
# Disclosure
# --------------------------------------------------------------------------- #
def show_disclosure() -> None:
    """Print the one-time disclosure (to stderr) and mark it shown. Idempotent."""
    if not is_enabled():
        return
    marker = _disclosure_path()
    try:
        if marker.exists():
            return
    except OSError:
        return
    print(_DISCLOSURE_TEXT, file=sys.stderr)
    try:
        _ensure_config_dir()
        marker.write_text("", encoding="utf-8")
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# Event emission
# --------------------------------------------------------------------------- #
def emit_started(surface: str) -> None:
    """Enqueue one ``ifixai_started`` event (idempotent per process).

    ``surface`` is ``"cli"`` or ``"plugin"`` so the two run paths stay separable.
    """
    global _started_emitted
    if _started_emitted:
        return
    _started_emitted = True
    _enqueue("ifixai_started", surface)


def emit_completed(surface: str) -> None:
    """Enqueue an ``ifixai_completed`` event for a finished run (idempotent per process)."""
    global _completed_emitted
    if _completed_emitted:
        return
    _completed_emitted = True
    _enqueue("ifixai_completed", surface)


def _build_payload(event: str, install: str, surface: str) -> bytes:
    # Build strictly FROM the allowlist so nothing outside it can ever be sent —
    # an `assert` would be stripped under `python -O`, silently killing the guard.
    # sorted() keeps the --print-telemetry output deterministic.
    source = {"version": VERSION, "os": platform.system(), "surface": surface}
    props: dict[str, object] = {key: source[key] for key in sorted(_ALLOWED_PROPS)}
    props["$ip"] = None  # PostHog: don't capture/derive geo from the request IP
    props["$geoip_disable"] = True
    envelope = {
        "api_key": POSTHOG_PROJECT_KEY,
        "event": event,
        "distinct_id": install,
        "properties": props,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    return json.dumps(envelope).encode("utf-8")


def _enqueue(event: str, surface: str) -> None:
    try:
        if not is_enabled():
            return
        install = install_id()
        if install is None:
            return
        _get_sender().submit(_build_payload(event, install, surface))
    except Exception:
        pass  # telemetry must never break a run


# --------------------------------------------------------------------------- #
# Background sender (daemon thread + atexit flush)
# --------------------------------------------------------------------------- #
class _Sender:
    def __init__(self) -> None:
        self._queue: "queue.Queue[bytes | None]" = queue.Queue()
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),  # bypass ambient http(s)_proxy
            urllib.request.HTTPSHandler(context=ssl.create_default_context()),
        )
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def submit(self, body: bytes) -> None:
        self._queue.put(body)

    def flush(self, budget: float) -> None:
        self._queue.put(None)  # sentinel: drain remaining items, then stop
        self._thread.join(timeout=budget)

    def _run(self) -> None:
        while True:
            body = self._queue.get()
            if body is None:
                return
            try:
                req = urllib.request.Request(
                    POSTHOG_ENDPOINT,
                    data=body,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with self._opener.open(req, timeout=_REQUEST_TIMEOUT) as resp:
                    resp.read()
            except Exception:
                pass  # dead/blocked endpoint must never raise


def _get_sender() -> "_Sender":
    global _sender
    with _sender_lock:
        if _sender is None:
            _sender = _Sender()
            atexit.register(_flush)
        return _sender


def _flush() -> None:
    if _sender is not None:
        try:
            _sender.flush(_FLUSH_BUDGET)
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# CLI affordances
# --------------------------------------------------------------------------- #
def print_payload() -> None:
    """``--print-telemetry``: print the exact ``started`` JSON without sending or creating an id."""
    install = _read_id() or "<install-id created on first real run>"
    body = json.loads(_build_payload("ifixai_started", install, "cli"))
    print(json.dumps(body, indent=2))


def show_id() -> str | None:
    """``--show-id``: return the stored install id (for erasure requests), or None."""
    return _read_id()
