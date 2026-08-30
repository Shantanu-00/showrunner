"""Host moderation + replay request/response shapes (spec 03 §5.3, spec 03 §6)."""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, Field

from .common import GuardianVerdict, MediaKind, Stage


class ReviewDecision(BaseModel):
    """The host's call on a `host_review` photo.

    Only three values are offered. `blocked` is deliberately absent: it is the SafeSearch gate's
    verdict, and a host wanting a photo gone entirely has delete, not a moderation label.
    """

    decision: GuardianVerdict = Field(
        description="public_ok | private_only | host_review (host_review returns it to the queue)"
    )
    note: str | None = Field(default=None, max_length=500)


class ReviewResponse(BaseModel):
    mediaId: str
    verdict: GuardianVerdict
    visibility: str | None = None


class ReplayResponse(BaseModel):
    mediaId: str
    stage: Stage
    queued: bool
    status: str | None = None


class ReviewQueueItem(BaseModel):
    """One photo awaiting the host's call, with everything the review card needs in one round trip.

    The Guardian's own `reasons` and `note` travel with it because the host is being asked to
    overrule a judgment, and a decision surface that does not say what the judgment *was* trains the
    host to click through it. `modelVerdict` is kept distinct from any later `hostDecision` for the
    same reason `api/moderation.py::review_media` writes to `guardian.hostDecision` rather than
    overwriting `guardian.verdict` — the model's answer stays on the record next to the human's.
    """

    mediaId: str
    kind: MediaKind = MediaKind.PHOTO
    modelVerdict: GuardianVerdict | None = None
    reasons: list[str] = Field(default_factory=list)
    note: str | None = None
    ritualEmotion: bool = False
    caption: str | None = None
    aestheticScore: float = 0.0
    #: Current exposure. Always `pool` or `self` for a queued item — shown so a host can see that
    #: nothing is public while they decide, which is the whole reason the queue is safe to leave.
    visibility: str | None = None
    uploadedAt: dt.datetime | None = None
    #: Set by the off-topic resolver (spec 04 §4's ranking factors, surfaced as prose). Advisory:
    #: nothing about exposure depends on it, and it is absent until that pass has run.
    offTopicNote: str | None = None


class ReviewQueueResponse(BaseModel):
    """`verdict` echoes which queue was asked for, so a client cannot mis-attribute the rows."""

    eventId: str
    verdict: GuardianVerdict
    items: list[ReviewQueueItem] = Field(default_factory=list)
    #: True when the scan hit its cap — the host has more to clear than this page shows.
    truncated: bool = False
