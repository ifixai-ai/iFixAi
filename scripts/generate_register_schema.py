#!/usr/bin/env python3
"""Write ``ifixai/schemas/misbehaviour-report-1.schema.json`` from the model.

The schema is generated, never hand-edited: two statements of one format drift, and the one CI
checks submissions against must be the one the library enforces. CI regenerates it and fails if the
committed file differs, so the schema in the repository is always the model's own.

A change that would make an existing report mean something different is a new spec version and a
new file (``...-2.schema.json``), never an edit of this one.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ifixai.register.spec import SPEC_VERSION, MisbehaviourReport  # noqa: E402

TARGET = ROOT / "ifixai" / "schemas" / f"misbehaviour-report-{SPEC_VERSION}.schema.json"


def build() -> str:
    schema = MisbehaviourReport.model_json_schema(mode="validation")
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = (
        "https://github.com/ifixai-ai/iFixAi/blob/main/ifixai/schemas/"
        f"misbehaviour-report-{SPEC_VERSION}.schema.json"
    )
    schema["title"] = f"iFixAi misbehaviour report, spec version {SPEC_VERSION}"
    schema["description"] = (
        "A reproducible report of AI-agent misbehaviour. Runtime-neutral: the behaviour is described in the "
        "report's own terms and bound to one or more runtimes under `reproductions`. See docs/register-spec.md "
        "for why each required field is required."
    )
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


def main() -> int:
    text = build()
    if "--check" in sys.argv:
        current = TARGET.read_text(encoding="utf-8") if TARGET.exists() else ""
        if current != text:
            print(f"{TARGET} is out of date; run python scripts/generate_register_schema.py", file=sys.stderr)
            return 1
        print(f"{TARGET.name} matches the model")
        return 0
    TARGET.write_text(text, encoding="utf-8")
    print(f"wrote {TARGET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
