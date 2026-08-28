"""What the Story Director's REASON step is allowed to say (spec 05 §1).

This is the narrowest surface in the system that a language model gets to influence, and the schema
is where that narrowness is enforced. Two constraints shaped it, both learned on the Curator
(`schemas/curator_out.py`):

**No free-form maps.** The response-schema dialect has no open-ended map, and a `dict[str, X]` field
comes back silently empty rather than rejected. Every field here is a scalar, an enum or a list of
scalars.

**No prose in `description=`.** The schema is billed on every request, roughly twice over (ADK sends
it as `response_schema` *and* as a JSON instruction). The rules live once in the agent instruction;
this file carries names and bounds.

The third constraint is architectural rather than economical: **this is one flat action shape, not a
union.** A discriminated union would be the prettier type, and it would also hand the model a schema
with `anyOf` branches it fills inconsistently — and, worse, it would let the *schema* be the
validation. It is not. `directors/story/act.py` re-validates every field of every action against the
event's real stage list, its real people and the spec 05 §1 guardrails before anything is written,
so a well-formed action is still rejected when it names a stage the event does not have. The schema
is a parsing convenience; the guardrails are the contract.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from .bounty import BountyAudience


class ActionType(str, Enum):
    """Spec 05 §1's action vocabulary, verbatim and closed."""

    ISSUE_BOUNTY = "ISSUE_BOUNTY"
    ESCALATE_BOUNTY = "ESCALATE_BOUNTY"
    PROPOSE_STAGE_ADVANCE = "PROPOSE_STAGE_ADVANCE"
    COMMISSION_REEL = "COMMISSION_REEL"
    ANNOUNCE = "ANNOUNCE"
    NO_OP = "NO_OP"


class DirectorAction(BaseModel):
    type: ActionType = ActionType.NO_OP
    reason: str = ""

    # ISSUE_BOUNTY
    targetStage: str | None = None
    targetMoment: str | None = None
    targetVip: str | None = None  # a personId from the ledger's VIP list, never a name
    title: str | None = None
    guestFacingCopy: str | None = None
    basePoints: int | None = None
    expiresInMin: int | None = None
    audience: BountyAudience | None = None

    # ESCALATE_BOUNTY
    bountyId: str | None = None

    # PROPOSE_STAGE_ADVANCE
    toStageId: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    evidence: str | None = None

    # COMMISSION_REEL
    persona: str | None = None
    stageId: str | None = None

    # ANNOUNCE
    kioskMessage: str | None = None


class DirectorPlan(BaseModel):
    """`{assessment, actions[]}` — spec 05 §1's REASON output.

    `assessment` is not decoration: it is what carries forward into the next tick's session window,
    so the model reasons over a narrative instead of a cold start (spec 05 §1), and it is what the
    host's wrap report quotes.
    """

    assessment: str = ""
    actions: list[DirectorAction] = Field(default_factory=list)
