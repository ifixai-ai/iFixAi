"""The behaviour signature: what makes two reports the same behaviour.

Two reports describe the same behaviour when they share a ``(family, failure, trigger)`` triple.
That is the private catalogue's clustering key, unchanged on purpose: a cluster found by a sweep
there and a report filed by a stranger here must land on the same entry, or the register
accumulates duplicates that only a human can spot.

The ``digest`` is a short, stable hash of the normalised triple together with the spec version. It
is derived, never authored — :func:`ifixai.register.validator.validate` recomputes it and rejects a
mismatch, so a submitter cannot slip past de-duplication by hand-editing the field.

Normalisation is deliberately shallow: case and surrounding whitespace are ignored, and runs of
separators collapse, so ``Authority Boundary`` and ``authority_boundary`` are one family. Nothing
beyond that is folded. "Nearly the same words" is a judgement for a curator, and a digest that
tried to make it would silently merge two different findings.
"""

from __future__ import annotations

import hashlib
import json
import re

from ifixai.register.spec import SPEC_VERSION, BehaviourSignature, MisbehaviourReport

_SEPARATORS = re.compile(r"[\s_\-./]+")
DIGEST_LENGTH = 12


def normalise(token: str) -> str:
    """Case-folded, separator-collapsed form of one signature component."""
    return _SEPARATORS.sub("_", token.strip().casefold()).strip("_")


def canonical_triple(signature: BehaviourSignature) -> dict:
    return {
        "spec_version": SPEC_VERSION,
        "family": normalise(signature.family),
        "failure": normalise(signature.failure),
        "trigger": normalise(signature.trigger),
    }


def digest(signature: BehaviourSignature) -> str:
    """The signature's short digest: the first 12 hex characters of the sha256 of the canonical
    triple. Short enough to read in a PR title, long enough that a collision between two real
    behaviours is not something anyone will see."""
    payload = json.dumps(canonical_triple(signature), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:DIGEST_LENGTH]


def with_digest(signature: BehaviourSignature) -> BehaviourSignature:
    return signature.model_copy(update={"digest": digest(signature)})


def same_behaviour(left: BehaviourSignature, right: BehaviourSignature) -> bool:
    return digest(left) == digest(right)


def candidate_ref(report: MisbehaviourReport) -> str:
    """The submitter's handle on their own draft: ``IFXC-<digest12>-<YYYYMMDD>``.

    Derived from the signature and the creation date, so re-drafting the same behaviour on the same
    day gives the same reference and a resubmission does not look like a second finding.
    """
    return f"IFXC-{digest(report.signature)}-{report.created_on.strftime('%Y%m%d')}"
