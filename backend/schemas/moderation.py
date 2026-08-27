"""Host moderation + replay request/response shapes (spec 03 §5.3, spec 03 §6)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from .common import GuardianVerdict, Stage


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
