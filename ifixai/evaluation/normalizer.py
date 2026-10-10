

import re
import unicodedata

from pydantic import BaseModel, Field

NORMALIZER_VERSION = "1.0.1"

_CODE_FENCE_RE = re.compile(r"```[a-zA-Z0-9_+-]*\n?(.*?)```", re.DOTALL)
_REASONING_TAG_RE = re.compile(
    r"<(thinking|reasoning|scratchpad)>.*?</\1>",
    re.DOTALL | re.IGNORECASE,
)
_REASONING_PREFIX_RE = re.compile(
    r"^(?:let me think[^.]*\.|let's think[^.]*\.|reasoning:|thinking:|first,?\s+let me)",
    re.IGNORECASE,
)
_WHITESPACE_RE = re.compile(r"\s+")

_PUNCTUATION_FOLD = str.maketrans({
    "\u2018": "'",
    "\u2019": "'",
    "\u201a": "'",
    "\u201b": "'",
    "\u201c": '"',
    "\u201d": '"',
    "\u201e": '"',
    "\u201f": '"',
    "\u2013": "-",
    "\u2014": "-",
    "\u2015": "-",
    "\u2026": "...",
    "\u00a0": " ",
})


class NormalizedText(BaseModel):

    raw: str
    normalized: str
    normalizer_version: str = Field(default=NORMALIZER_VERSION)


def normalize(raw: str) -> NormalizedText:
    if raw is None:
        raise ValueError("normalize() requires a string, got None")

    working = raw

    working = _REASONING_TAG_RE.sub("", working)

    fence_match = _CODE_FENCE_RE.search(working)
    if fence_match:
        working = fence_match.group(1)

    working = working.strip()
    working = _REASONING_PREFIX_RE.sub("", working).strip()

    working = unicodedata.normalize("NFKC", working)
    working = working.translate(_PUNCTUATION_FOLD)
    working = _WHITESPACE_RE.sub(" ", working).strip()

    return NormalizedText(raw=raw, normalized=working)
