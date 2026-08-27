"""The Guardian's structured output — pass 2 only (spec 03 §5.3).

What the model returns here is deliberately *not* the stored verdict. It returns observations plus
a proposed verdict; `workers.safety.gate` then combines that with the SafeSearch floor, the host's
declared sensitivity dials and the hard `minor_prominent` rule, and only that deterministic
combination is written to the document. The reason is spec 04 §1: a language model may inform
exposure, it may never decide it.

The reason list is closed on purpose. A free-text reason would be untestable, unqueryable and
impossible to render in the host review card, and the dial logic in `gate.py` has to key off exact
values — a model inventing `slightly_awkward_pose` would silently escape every clamp.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from .common import GuardianVerdict


class DignityReason(str, Enum):
    """Machine-readable reasons. The first six are spec 03 §5.3 verbatim.

    The last three are *observations of the host's declared dials* (spec 11 §2's `pda`, `alcohol`,
    `attire`): the model reports what is in the frame, and `gate.py` — not the model — decides what
    the host's dial setting means for exposure. Without a reason code per dial there is nothing for
    the deterministic ceiling to key off, and "the event-level dial is a ceiling" would have to be
    implemented inside the prompt, i.e. not implemented at all.
    """

    EYES_CLOSED = "eyes_closed"
    MID_BITE = "mid_bite"
    WARDROBE_RISK = "wardrobe_risk"
    DISTRESS_OUT_OF_CONTEXT = "distress_out_of_context"
    UNFLATTERING_ANGLE = "unflattering_angle"
    MINOR_PROMINENT = "minor_prominent"
    PDA_VISIBLE = "pda_visible"
    ALCOHOL_VISIBLE = "alcohol_visible"
    ATTIRE_REVEALING = "attire_revealing"


class GuardianOut(BaseModel):
    """One photo in, one dignity judgment out."""

    verdict: GuardianVerdict = GuardianVerdict.HOST_REVIEW
    reasons: list[DignityReason] = Field(default_factory=list)
    #: One short sentence for the host's review card. Never shown to guests.
    note: str | None = None
    #: True when the emotion in the frame is *ritual* rather than distress (tears at Kanyadaan).
    #: Stored as part of the reasoning trail, and the single most common judgment call this agent
    #: makes — spec 03 §5.3 names it as the example of what a rule table could not do.
    ritualEmotion: bool = False
