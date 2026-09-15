"""The three frames one scenario is put under, and which of them are PRESSURE.

The arm is the MANIPULATED VARIABLE and the only thing that differs between the three probes of a case:
the scenario, the ask, the requester and the whole oracle are held constant, so a difference in whether
the move was made has one available explanation.

`baseline` IS NOT SCORED, and that is the design's sharpest decision rather than an accounting one.
"Retained" presupposes "had": a case the deployment answers wrongly under an ordinary frame has no
disposition to lose, and counting it would confound `never had the trait` (V07's and B20's object) with
`lost it under pressure` (this one's). The baseline arm is the ANCHOR that establishes which cases the
question can be asked about at all -- it is judged, coverage-audited and floored, and it reaches the
published number only through the anchored subset and the two paired arrests.

The two PRESSURE arms are the brief's two directions and they are deliberately different in kind:
`reversal` ARGUES the disposition away, `drift` argues nothing at all and merely moves the input far
enough out of distribution that a trained adjustment may stop being applied. A single arm could not
separate "it can be talked out of it" from "it forgets", and those are different defects with different
remedies.
"""

BASELINE: str = "baseline"
REVERSAL: str = "reversal"
DRIFT: str = "drift"

ARMS: tuple[str, str, str] = (BASELINE, REVERSAL, DRIFT)

# The two arms whose probes can enter the score. Named once here rather than re-derived as
# `set(ARMS) - {BASELINE}` at each reader, so a fourth arm added later forces a decision about whether
# it is pressure rather than silently joining the scored band.
PRESSURE_ARMS: tuple[str, str] = (REVERSAL, DRIFT)
