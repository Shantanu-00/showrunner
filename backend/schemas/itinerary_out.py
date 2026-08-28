"""What the itinerary-parse model is allowed to return (spec 08 §3.2).

Deliberately does not include real `startsAt`/`endsAt` timestamps. A pasted WhatsApp itinerary
names times of day ("4 PM", "evening") on a date that is either implicit, ambiguous across a
multi-day wedding, or simply absent — inferring a UTC instant from that text is a different, much
less reliable problem than extracting the *structure* (stage names, their order, what moments each
one names), and spec 08 §3.2's own discipline is that the host, not the model, is the one who
"confirms or fixes before anything downstream trusts it". So this schema stops at `timeHint` (the
time-of-day text the model actually saw) and leaves the reviewable date/time picker to the host
console; `PUT /v1/events/{eventId}/stages` is where a real `schemas.event.EventStage` with real
UTC windows gets written, from the host-edited table, not from this response directly.

Same two hard-won rules as `curator_out.py`: no free-form maps (each stage's moments are a list of
fixed-key objects), no prose in `Field(description=...)` — the instruction text in
`backend/api/host.py::ITINERARY_INSTRUCTION` is billed once; a schema description is billed twice.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ParsedMoment(BaseModel):
    momentId: str  # lowercase snake_case slug, reused verbatim by the Curator/Story Director if kept
    label: str


class ParsedStage(BaseModel):
    stageId: str  # lowercase snake_case slug
    label: str
    orderIndex: int = 0
    timeHint: str = ""  # the time-of-day text as pasted, e.g. "4:00 PM" — never a computed instant
    requiredMoments: list[ParsedMoment] = Field(default_factory=list)


class ItineraryParseOut(BaseModel):
    """One paste in, a proposed (never authoritative) stage table out."""

    stages: list[ParsedStage] = Field(default_factory=list)
    #: e.g. "no explicit stage boundaries found — the whole paste read as one stage." Shown to the
    #: host verbatim above the review table; never blocks anything, since Go Live's real gate is
    #: "≥1 stage", not "the parse was confident".
    warnings: list[str] = Field(default_factory=list)
