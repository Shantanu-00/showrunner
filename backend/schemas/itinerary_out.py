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
    #: Spec 13's amendment to the module docstring above: when the *event's own date range* is in
    #: the prompt and the source states or plainly implies which day a stage falls on ("Day 3",
    #: "Oct 14", "Tuesday"), the model may anchor the timeHint to it as a proposed local instant,
    #: "YYYY-MM-DDTHH:MM" in the event's timezone. This is a **prefill for the host's date/time
    #: picker, never a saved value** — the review-table discipline is unchanged, and `PUT /stages`
    #: remains the only writer of real UTC windows. Empty whenever the day is not in the text:
    #: "never invent" still wins over "convenient". Typed `str` so a malformed value degrades to an
    #: unfilled picker rather than failing the parse.
    proposedStartLocal: str = ""
    proposedEndLocal: str = ""
    requiredMoments: list[ParsedMoment] = Field(default_factory=list)
    #: A `SceneSetting` value, or "" when the paste does not say. Typed `str` for the same reason
    #: `CuratorOut.sceneSetting` is: an unrecognised value must degrade, not fail the whole parse.
    #:
    #: This is the world model's **cold-start prior**, and it is the piece that makes the whole feature
    #: work at all. A distribution needs a distribution: photo #1 is 100% of the corpus, and a wedding
    #: that starts indoors and moves outdoors for the baraat would have `outdoor_venue` at 0% of the
    #: corpus at photo 15 — so the baraat, the most important sequence of the day, would read as a
    #: 100% outlier. An expected setting per stage, declared before a single photo arrives, is the only
    #: thing that fixes that; nothing derived from the photos themselves can.
    #:
    #: Extracted here but **never authoritative** — like every other field on this model, it goes into
    #: the host's review table and only `PUT /v1/events/{eventId}/stages` writes it for real. That
    #: matters more for this field than the others: it makes the prior a *host-confirmed declaration*
    #: rather than a model assertion, which is the same posture spec 11 §2 takes for the cultural
    #: glossary and the sensitivity dials.
    expectedSetting: str = ""


class ItineraryParseOut(BaseModel):
    """One paste (or PDF/screenshot, spec 13) in, a proposed (never authoritative) stage table out."""

    stages: list[ParsedStage] = Field(default_factory=list)
    #: Only when the input was a document or image: the plain-text schedule the model read out of
    #: it. Model Armor cannot pre-screen bytes it never sees as text (§4.7 — Armor is text-only),
    #: so the endpoint guards *this* after the parse and rejects the whole proposal on a block —
    #: the same "checked at onboarding" property the pasted path gets up front. Instructed to stay
    #: empty for plain-text input, where the paste was already guarded and echoing 8k chars back
    #: would be pure output-token spend.
    sourceText: str = ""
    #: e.g. "no explicit stage boundaries found — the whole paste read as one stage." Shown to the
    #: host verbatim above the review table; never blocks anything, since Go Live's real gate is
    #: "≥1 stage", not "the parse was confident".
    warnings: list[str] = Field(default_factory=list)
