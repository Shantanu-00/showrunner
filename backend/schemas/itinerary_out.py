"""What the itinerary-parse model returns (spec 08 §3.2, spec 13).

Extracts the full holistic event structure: suggested title, dates, timezone, participant count,
access mode, cultural glossary, key people/VIPs, and a rich chronological stage table.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ParsedMoment(BaseModel):
    momentId: str  # lowercase snake_case slug, reused verbatim by the Curator/Story Director if kept
    label: str


class ParsedPerson(BaseModel):
    name: str
    role: str = ""  # e.g. "Bride", "Groom", "Speaker", "Host", "Traveler", "VIP"
    tier: int = 2  # 0=Principal, 1=Inner Circle, 2=Named VIP, 3=Guest


class ParsedStage(BaseModel):
    stageId: str  # lowercase snake_case slug
    label: str
    orderIndex: int = 0
    timeHint: str = ""  # the time-of-day text as pasted, e.g. "4:00 PM"
    proposedStartLocal: str = ""  # "YYYY-MM-DDTHH:MM" in event's local timezone
    proposedEndLocal: str = ""
    requiredMoments: list[ParsedMoment] = Field(default_factory=list)
    expectedSetting: str = ""  # indoor_venue, outdoor_venue, outdoor_nature, domestic_interior, vehicle, street
    theme: str = ""  # e.g. golden_hour, candlelight, champagne, neon_afterparty, sunset, daylight, twilight, sage_botanical, ocean, midnight


class ItineraryParseOut(BaseModel):
    """Full AI-extracted event configuration from pasted text, PDF, or screenshot."""

    suggestedName: str = ""  # synthesized or extracted event name
    startDate: str = ""  # "YYYY-MM-DD" or empty
    endDate: str = ""  # "YYYY-MM-DD" or empty
    timezone: str = ""  # IANA timezone (e.g. "Asia/Tokyo", "America/New_York")
    expectedParticipants: int | None = None  # extracted headcount or null
    suggestedAccessMode: str = "invite"  # "invite" (private/family/trip) or "open" (public venue/party)
    suggestedPeople: list[ParsedPerson] = Field(default_factory=list)
    culturalGlossary: list[str] = Field(default_factory=list)
    stages: list[ParsedStage] = Field(default_factory=list)
    sourceText: str = ""
    warnings: list[str] = Field(default_factory=list)

