"""The public register of reproducible AI-agent misbehaviour.

A *report* is to agent misbehaviour what a CVE record is to a software flaw: a named behaviour,
stated precisely enough that a stranger can reproduce it. This package holds the format
(:mod:`ifixai.register.spec`), the behaviour signature used to cluster and de-duplicate reports
(:mod:`ifixai.register.signature`), the validator every submission must pass
(:mod:`ifixai.register.validator`), the local de-identifier (:mod:`ifixai.register.scrub`), and
the contribution path (:mod:`ifixai.register.draft`, :mod:`ifixai.register.approval`,
:mod:`ifixai.register.submit`).

The format is **runtime-neutral**. A report describes the world, the identities, the tools, the
request, the expected final state and the grading checks in its own terms; a *reproduction
implementation* then binds that description to a concrete runtime. Harbor is the reference
implementation, not the definition, so a report stays readable and checkable without it and a
second runtime can be added to an existing report without reissuing its id.
"""

from ifixai.register.spec import SPEC_VERSION, MisbehaviourReport

__all__ = ["SPEC_VERSION", "MisbehaviourReport"]
