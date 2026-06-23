"""Bridge transports for the iFixAi Claude plugin (Phase 0 spike).

The plugin's design routes the engine's two
model-I/O seams — the SUT ``ChatProvider`` and the judge ``ChatProvider`` —
through ``BridgeProvider``. Its ``send_message`` is a thin shim that hands the
fully-rendered messages to a swappable *transport* and returns the raw reply.
Everything between the seams (inspection selection, template rendering, the 45
runners, judge prompt construction, verdict parsing, scoring) is the unmodified
engine. This is the engine side of the plan's "only two network substitutions".

Transports (offline rehearsal only — a live run uses the engine's native
providers via the orchestrator's ``--mode api``, not a bridge transport):
  * ``RecordingTransport`` / ``ReplayTransport`` — deterministic record & replay,
    the development substrate and the golden-parity harness (plan R3).
  * ``ConstantTransport`` / ``StubJudgeTransport`` — canned replies for the spike
    and tests, with no model access at all.

Replay keys normalize 16-hex nonces out of the prompt: the judge envelope nonce
(``secrets.token_hex(8)``) and the SUT ``run_nonce`` vary per run and would
otherwise defeat content-keyed lookup. The verdict carries no nonce echo, so a
recorded reply replays cleanly against a freshly-nonced prompt.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from pathlib import Path
from typing import Optional

from ifixai.core.types import ChatMessage, ProviderConfig
from ifixai.providers.base import ChatProvider

# B09's rubric lists dimensions as "  - <name> [MANDATORY]: ...". Reused here so
# the stub judge synthesises the exact contract the engine's parser expects
# (mirrors MockGovernanceProvider._judge_response).
_DIM_NAME_RE = re.compile(r"^\s*-\s+(\S+?)(?:\s+\[MANDATORY\])?:", re.MULTILINE)

# Any 16-char lowercase-hex run — the shape of both the judge envelope nonce and
# the SUT run_nonce marker. Normalized to a placeholder before keying so record
# and replay collide on the same key despite per-run nonces.
_NONCE_RE = re.compile(r"[0-9a-f]{16}")

SUT_CHANNEL = "sut"
JUDGE_CHANNEL = "judge"

_logger = logging.getLogger(__name__)


class BridgeTransportError(RuntimeError):
    """A bridge transport failed to produce a reply."""


# --------------------------------------------------------------------------- #
# Replay keying
# --------------------------------------------------------------------------- #
def replay_key(messages: list[ChatMessage], config: ProviderConfig, channel: str) -> str:
    """Stable content hash for (channel, model, messages), nonce-insensitive."""
    body = "\n".join(f"{m.role}\x1f{m.content}" for m in messages)
    payload = f"{channel}\x1e{config.model or ''}\x1e{body}"
    payload = _NONCE_RE.sub("<NONCE>", payload)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# Transports
# --------------------------------------------------------------------------- #
class Transport:
    """A model-I/O backend: fully-rendered messages in, raw reply text out."""

    async def complete(
        self, messages: list[ChatMessage], config: ProviderConfig, channel: str
    ) -> str:
        raise NotImplementedError

    async def aclose(self) -> None:
        return None


class ConstantTransport(Transport):
    """Always returns the same reply. Used as a canned SUT in the spike."""

    def __init__(self, response: str) -> None:
        self._response = response

    async def complete(
        self, messages: list[ChatMessage], config: ProviderConfig, channel: str
    ) -> str:
        return self._response


class StubJudgeTransport(Transport):
    """Synthesises a contract-valid verdict for whatever dimensions the judge
    prompt lists. ``passed`` makes every dimension pass (or fail) so the spike
    can produce a deterministic, known grade with no model access."""

    def __init__(self, passed: bool = True) -> None:
        self._passed = passed

    def verdict_for(self, prompt: str) -> str:
        # The atomic-claims judge sends a different contract (a JSON list of
        # {claim, supported, reason}); answer it in kind so stub runs don't
        # spam "judge returned zero claims" retry warnings.
        if "atomic factual claims" in prompt:
            reason = "stub pass" if self._passed else "stub fail"
            return json.dumps(
                [{"claim": "stub claim", "supported": self._passed, "reason": reason}]
            )
        names = _DIM_NAME_RE.findall(prompt)
        if not names:
            # Fall back to a single generic dimension so parsing never starves.
            names = ["overall"]
        reason = "stub pass" if self._passed else "stub fail"
        dims = [{"name": n, "passed": self._passed, "reasoning": reason} for n in names]
        return json.dumps({"dimensions": dims, "overall_reasoning": "stub evaluation"})

    async def complete(
        self, messages: list[ChatMessage], config: ProviderConfig, channel: str
    ) -> str:
        return self.verdict_for("\n".join(m.content for m in messages))


class ModelRoutedJudgeTransport(Transport):
    """Stub judge that returns an all-pass or all-fail verdict per judge *model*.

    An ensemble routes each distinct-model judge onto the same "judge" channel
    but with a different `config.model`. Keying the verdict on the model lets the
    judges diverge, exercising the engine's mean/majority/veto aggregation and
    proving the bridge routed to genuinely distinct judges."""

    def __init__(self, verdict_by_model: dict[str, bool], default: bool = True) -> None:
        self._by_model = verdict_by_model
        self._default = default

    async def complete(
        self, messages: list[ChatMessage], config: ProviderConfig, channel: str
    ) -> str:
        passed = self._by_model.get(config.model or "", self._default)
        prompt = "\n".join(m.content for m in messages)
        return StubJudgeTransport(passed=passed).verdict_for(prompt)


class RecordingTransport(Transport):
    """Wraps an inner transport, capturing every reply into ``store`` keyed by
    the nonce-insensitive replay key. Save ``store`` to replay later (R3)."""

    def __init__(self, inner: Transport, store: dict[str, dict]) -> None:
        self._inner = inner
        self._store = store

    async def complete(
        self, messages: list[ChatMessage], config: ProviderConfig, channel: str
    ) -> str:
        key = replay_key(messages, config, channel)
        reply = await self._inner.complete(messages, config, channel)
        self._store[key] = {
            "channel": channel,
            "model": config.model,
            "response": reply,
        }
        return reply

    async def aclose(self) -> None:
        await self._inner.aclose()


class CachingTransport(Transport):
    """Resume cache: serve a recorded reply when the prompt was already seen,
    otherwise call `inner` and record it. Restarting an interrupted run reuses
    every prior reply (no re-billing) and only does the remaining work — the
    plan's manifest checkpoint/resume, at the model-I/O seam where the billable
    artifacts actually live. Pass `on_record` to persist after each new reply so
    resume survives a process exit mid-run."""

    def __init__(self, inner: Transport, store: dict[str, dict], on_record=None) -> None:
        self._inner = inner
        self._store = store
        self._on_record = on_record

    async def complete(
        self, messages: list[ChatMessage], config: ProviderConfig, channel: str
    ) -> str:
        key = replay_key(messages, config, channel)
        cached = self._store.get(key)
        if cached is not None:
            return cached["response"]
        reply = await self._inner.complete(messages, config, channel)
        self._store[key] = {"channel": channel, "model": config.model, "response": reply}
        if self._on_record is not None:
            self._on_record(self._store)
        return reply

    async def aclose(self) -> None:
        await self._inner.aclose()


class ReplayTransport(Transport):
    """Returns recorded replies. A miss raises, so an incomplete recording is
    loud rather than silently wrong."""

    def __init__(self, store: dict[str, dict]) -> None:
        self._store = store

    async def complete(
        self, messages: list[ChatMessage], config: ProviderConfig, channel: str
    ) -> str:
        key = replay_key(messages, config, channel)
        entry = self._store.get(key)
        if entry is None:
            raise BridgeTransportError(
                f"replay miss on channel '{channel}' (key {key[:12]}…); "
                "the recording does not cover this prompt"
            )
        return entry["response"]


# --------------------------------------------------------------------------- #
# Transport registry + BridgeProvider
# --------------------------------------------------------------------------- #
_TRANSPORTS: dict[str, Transport] = {}


def set_transport(channel: str, transport: Transport) -> None:
    _TRANSPORTS[channel] = transport


def get_transport(channel: str) -> Transport:
    transport = _TRANSPORTS.get(channel)
    if transport is None:
        raise BridgeTransportError(
            f"no bridge transport registered for channel '{channel}'; "
            f"call set_transport('{channel}', ...) before running"
        )
    return transport


def clear_transports() -> None:
    _TRANSPORTS.clear()


class BridgeProvider(ChatProvider):
    """SUT seam. ``send_message`` delegates to the transport for its channel."""

    channel = SUT_CHANNEL

    def __init__(self, channel: Optional[str] = None) -> None:
        if channel is not None:
            self.channel = channel

    async def send_message(
        self, messages: list[ChatMessage], config: ProviderConfig
    ) -> str:
        return await get_transport(self.channel).complete(messages, config, self.channel)

    async def aclose(self) -> None:
        return None


class BridgeJudgeProvider(BridgeProvider):
    """Judge seam. Resolved by name ("bridge") so ``JudgeConfig(provider="bridge")``
    routes judge ``send_message`` calls onto the judge channel. Constructed with
    no args by the provider factory."""

    channel = JUDGE_CHANNEL

    def __init__(self) -> None:
        super().__init__(channel=JUDGE_CHANNEL)


# --------------------------------------------------------------------------- #
# Recording (de)serialization
# --------------------------------------------------------------------------- #
def save_recording(store: dict[str, dict], path: str | Path) -> None:
    # Atomic write: a checkpoint is rewritten after every reply, so a kill
    # mid-write must not leave a truncated file that breaks the next resume.
    path = Path(path)
    payload = {"version": 1, "entries": store}
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), "utf-8")
    # Checkpoints live in a shared temp dir and hold full SUT replies to
    # jailbreak/injection probes — keep them owner-only, not world-readable.
    # chmod the temp file before the atomic replace so there is no 0644 window.
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def load_recording(path: str | Path) -> dict[str, dict]:
    payload = json.loads(Path(path).read_text("utf-8"))
    return payload["entries"]
