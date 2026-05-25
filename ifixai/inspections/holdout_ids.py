"""Per-run holdout ID generation for B01, B04, and B16 inspections.

Holdout IDs are opaque strings sent to providers during inspections that test
denial behaviour. Generating them fresh per run (or deterministically from a
seed) prevents providers from pattern-matching on literal constants.

Contract: `generate_holdout_ids` is the pipeline's responsibility (cli/run.py).
Inspection runners must not call it directly — they receive IDs via
`ProviderConfig.holdout_ids` and raise `ConfigError` if any required key is
absent or empty.
"""

import random
import uuid
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class HoldoutIds:
    b01_tool_id: str
    b01_role: str
    b04_request_a: str
    b04_request_b: str
    b04_request_flip: str
    b16_user_nonexistent: str
    b16_user_revoked: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def _make_uuid(rng: random.Random | None) -> str:
    if rng is None:
        return str(uuid.uuid4())
    return str(uuid.UUID(int=rng.getrandbits(128), version=4))


def generate_holdout_ids(seed: int | None = None) -> HoldoutIds:
    """Return a HoldoutIds with unpredictable values.

    When seed is None each call returns distinct random UUIDs.
    When seed is an integer the same IDs are produced deterministically,
    enabling replay of a prior run.
    """
    rng = random.Random(seed) if seed is not None else None
    return HoldoutIds(
        b01_tool_id=f"holdout-tool-{_make_uuid(rng)}",
        b01_role=f"holdout-role-{_make_uuid(rng)}",
        b04_request_a=f"holdout-override-a-{_make_uuid(rng)}",
        b04_request_b=f"holdout-override-b-{_make_uuid(rng)}",
        b04_request_flip=f"holdout-flip-{_make_uuid(rng)}",
        b16_user_nonexistent=f"holdout-user-{_make_uuid(rng)}",
        b16_user_revoked=f"holdout-revoked-{_make_uuid(rng)}",
    )
