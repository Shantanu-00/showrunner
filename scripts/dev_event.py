"""Create an `internal_dev` event so the upload path can be exercised end to end.

This is the admin-side shortcut, not the product: the real onboarding wizard (`POST /v1/events`,
the capacity cap, the class assignment) is spec 08 / spec 11 and lands in a later session. What
this does give you is a *correctly shaped* Event Graph — timezone, stage windows, cultural
profile — because a wrong-shaped one makes the Curator's temporal prior silently meaningless.

    python scripts/dev_event.py                 # new event, stages centred on now
    python scripts/dev_event.py --event-id dev  # stable id you can bookmark
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from enum import Enum
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

from schemas.event import (  # noqa: E402
    DemoConfig,
    Event,
    EventClass,
    EventStage,
    EventStatus,
    EventTypeProfile,
    RequiredMoment,
    SensitivityProfile,
    VipTopology,
)
from shared import fs  # noqa: E402
from shared.settings import settings  # noqa: E402
from shared.ulid import new_ulid  # noqa: E402


def firestore_ready(value: Any) -> Any:
    """Enums to their string values, datetimes left alone.

    `mode="json"` would stringify the timestamps, and a stage window stored as a string can
    never be compared to a photo's `capturedAt`.
    """
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {k: firestore_ready(v) for k, v in value.items()}
    if isinstance(value, list):
        return [firestore_ready(v) for v in value]
    return value


def build_stages(now: dt.datetime, tz: ZoneInfo) -> list[EventStage]:
    """Three stages around `now`, so a photo taken this minute lands inside a window.

    Windows are stored UTC; EXIF is interpreted in the event timezone before comparison
    (spec 03 §5.1). The middle stage is active.
    """
    local = now.astimezone(tz)
    anchor = local.replace(minute=0, second=0, microsecond=0)

    def utc(hours: float) -> dt.datetime:
        return (anchor + dt.timedelta(hours=hours)).astimezone(dt.timezone.utc)

    return [
        EventStage(
            stageId="haldi",
            label="Haldi",
            startsAt=utc(-4),
            endsAt=utc(-1),
            theme="turmeric",
            requiredMoments=[RequiredMoment(momentId="haldi_smear", label="Haldi smear")],
        ),
        EventStage(
            stageId="sangeet",
            label="Sangeet",
            startsAt=utc(-1),
            endsAt=utc(3),
            theme="night",
            requiredMoments=[
                RequiredMoment(momentId="first_dance", label="First dance", tierWeight=1.5),
                RequiredMoment(momentId="family_group", label="Family group photo"),
            ],
        ),
        EventStage(
            stageId="ceremony",
            label="Ceremony",
            startsAt=utc(3),
            endsAt=utc(7),
            theme="dawn",
            requiredMoments=[RequiredMoment(momentId="kanyadaan", label="Kanyadaan", tierWeight=2.0)],
        ),
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description="Create or refresh an internal_dev event.")
    ap.add_argument("--event-id", default=None, help="defaults to a fresh ULID")
    ap.add_argument("--name", default="Showrunner Dev Event")
    ap.add_argument("--timezone", default="Asia/Kolkata")
    ap.add_argument("--status", default=EventStatus.LIVE.value, choices=[s.value for s in EventStatus])
    args = ap.parse_args()

    cfg = settings()
    cfg.require("project")

    event_id = args.event_id or f"dev_{new_ulid()}"
    tz = ZoneInfo(args.timezone)
    now = dt.datetime.now(dt.timezone.utc)

    event = Event(
        eventId=event_id,
        name=args.name,
        timezone=args.timezone,
        status=EventStatus(args.status),
        # Server-assigned only (spec 11 §1.1). internal_dev is exempt from the capacity cap and
        # the public TTL, which is exactly why a client can never ask for it.
        **{"class": EventClass.INTERNAL_DEV},
        stages=build_stages(now, tz),
        activeStage="sangeet",
        eventTypeProfile=EventTypeProfile(
            vipTopology=VipTopology.PYRAMID,
            sensitivityProfile=SensitivityProfile(),
            culturalGlossary=["haldi", "sangeet", "kanyadaan", "baraat", "mangalsutra"],
        ),
        demoConfig=DemoConfig(enabled=False),
        createdAt=now,
        liveAt=now if args.status == EventStatus.LIVE.value else None,
    )

    payload = firestore_ready(event.model_dump(by_alias=True))
    fs.event_ref(event_id).set(payload, merge=True)

    print(f"event:    {event_id}")
    print(f"project:  {cfg.project}")
    print(f"status:   {event.status.value}  class: {event.eventClass.value}")
    print(f"timezone: {args.timezone}  activeStage: {event.activeStage}")
    print()
    print(f"export SMOKE_EVENT_ID={event_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
