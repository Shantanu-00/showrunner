"""Create/refresh the one standing `protected_demo` event: a global, always-on demo anyone with the
link can join and upload to — no venue, no cast, no theme, no pre-seeded photos.

    python scripts/seed_global_event.py
    python scripts/seed_global_event.py --weeks 10 --daily-cap 40 --lifetime-cap 1500

Replaces the old judge-mode wedding seeder. Three differences from that script, all deliberate:

1. **No fixture uploads, no AI cast.** The old script wrapped `backend/seed.py`'s Hindu-wedding cast
   and golden-fixture set. This one writes only the Event document — zero media. Real visitors are
   the only source of content; the wall is empty until someone's phone puts something on it.
2. **A volume ceiling instead of a time limit.** `class: protected_demo` already exempts this event
   from the public 60-minute TTL and the $3 cost ceiling (spec 11 §1) — it has to stay live
   indefinitely, so time can't be the guardrail. `dailyMediaCap`/`lifetimeMediaCap`
   (`schemas/event.py::Event`) bound spend instead: a fixed number of Gemini/Vision calls this event
   will ever generate, no matter how long it runs. `reelCommissionEveryNMedia` does the same job for
   reel renders (Veo/Lyria are the expensive step) — a trickle of daily uploads no longer earns a
   fresh highlight film on every director tick.
3. **A multi-week timeline instead of wedding stages.** No sangeet, no haldi — the stages below are
   generic time-chapters ("Week 1: First Signals", …) that make sense for photos arriving from
   anywhere, on no fixed schedule.

`class: protected_demo` can only be minted by a script running with the deployment owner's own ADC
credentials (spec 11 §1.1) — never settable through the public API. Same posture as before.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from api import host as host_api  # noqa: E402
from schemas.event import (  # noqa: E402
    DemoConfig,
    Event,
    EventClass,
    EventStage,
    EventStatus,
    RequiredMoment,
)
from shared import fs  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

GLOBAL_EVENT_ID = "global_demo"
GLOBAL_EVENT_NAME = "Showrunner — Global Demo"

#: Anyone's test shot should reach the wall, same reasoning as the old judge event (spec 09 §5):
#: with the floor at 0, consent + Guardian alone decide `public`, so a first-timer's photo of their
#: desk clears in seconds instead of silently sitting in `pool` and reading as breakage.
GLOBAL_PUBLIC_FLOOR = 0.0

#: NOT spec-pinned — flagged here rather than silently chosen. Deliberately small: this event never
#: wraps on a timer, so its lifetime spend is (roughly) lifetime_cap × per-photo perception cost,
#: and the point of running it at all is that number staying trivially small for as long as the
#: event lives.
DEFAULT_DAILY_CAP = 40
DEFAULT_LIFETIME_CAP = 1500
DEFAULT_REEL_EVERY_N = 60
DEFAULT_WEEKS = 8

HOST_LINK_TTL_DAYS = 120


def _build_stages(now: dt.datetime, weeks: int) -> list[EventStage]:
    """Week-long chapters, not venue beats — the whole point is that nobody uploading here shares a
    place or a schedule. Labels and prompts stay generic enough to mean something for a photo taken
    anywhere: "wherever you are", never "at the venue"."""
    labels = [
        ("first_signals", "Week 1 — First Signals", "wherever you are right now"),
        ("around_the_world", "Week 2 — Around the World", "a place you can see from where you stand"),
        ("golden_hour_everywhere", "Week 3 — Golden Hour, Everywhere", "the light right now, wherever you are"),
        ("faces_and_places", "Week 4 — Faces & Places", "someone you're with"),
        ("the_long_tail", "Week 5 — The Long Tail", "something in motion"),
        ("still_going", "Week 6 — Still Going", "a small detail worth noticing"),
        ("the_home_stretch", "Week 7 — The Home Stretch", "your view right now"),
        ("last_call", "Week 8 — Last Call", "one more, before it wraps"),
    ]
    stages: list[EventStage] = []
    for i in range(weeks):
        slug, label, prompt = labels[i % len(labels)]
        stage_id = slug if i < len(labels) else f"{slug}_{i // len(labels) + 1}"
        starts = now + dt.timedelta(weeks=i)
        ends = now + dt.timedelta(weeks=i + 1)
        stages.append(
            EventStage(
                stageId=stage_id,
                label=label if i < len(labels) else f"{label} (encore)",
                startsAt=starts,
                endsAt=ends,
                requiredMoments=[RequiredMoment(momentId=f"{stage_id}_moment", label=prompt, tierWeight=1.0)],
            )
        )
    return stages


def ensure_global_event(
    event_id: str,
    *,
    weeks: int,
    daily_cap: int,
    lifetime_cap: int,
    reel_every_n: int,
) -> dict:
    now = dt.datetime.now(dt.timezone.utc)
    today = now.date().isoformat()
    ends_on = (now.date() + dt.timedelta(weeks=weeks)).isoformat()
    event = Event(
        eventId=event_id,
        name=GLOBAL_EVENT_NAME,
        timezone="UTC",  # deliberately not local to anywhere — uploads arrive from everywhere
        status=EventStatus.LIVE,
        **{"class": EventClass.PROTECTED_DEMO},
        startsOn=today,
        endsOn=ends_on,
        stages=_build_stages(now, weeks),
        activeStage=None,  # resolves off the stage windows above, never a manual override
        demoConfig=DemoConfig(enabled=True, compressedTimeline=False),
        publicFloor=GLOBAL_PUBLIC_FLOOR,
        dailyMediaCap=daily_cap,
        lifetimeMediaCap=lifetime_cap,
        reelCommissionEveryNMedia=reel_every_n,
        createdAt=now,
        liveAt=now,
    )
    payload = fs.to_firestore(event.model_dump(by_alias=True))
    fs.event_ref(event_id).set(payload, merge=True)
    return fs.get_event(event_id) or {}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--event-id", default=GLOBAL_EVENT_ID)
    ap.add_argument("--weeks", type=int, default=DEFAULT_WEEKS, help="how many week-stages to lay out")
    ap.add_argument("--daily-cap", type=int, default=DEFAULT_DAILY_CAP)
    ap.add_argument("--lifetime-cap", type=int, default=DEFAULT_LIFETIME_CAP)
    ap.add_argument("--reel-every", type=int, default=DEFAULT_REEL_EVERY_N)
    ap.add_argument("--no-host-link", action="store_true", help="do not mint a host link")
    args = ap.parse_args()

    print(f"Creating/refreshing the global demo event `{args.event_id}` (class=protected_demo)\n")

    event = ensure_global_event(
        args.event_id,
        weeks=args.weeks,
        daily_cap=args.daily_cap,
        lifetime_cap=args.lifetime_cap,
        reel_every_n=args.reel_every,
    )
    print(f"      event ready (status={event.get('status')}, class={event.get('class')})")
    print(f"      caps: {args.daily_cap}/day, {args.lifetime_cap} lifetime, a reel every {args.reel_every} new uploads")
    print(f"      timeline: {args.weeks} week-stages, {event.get('startsOn')} -> {event.get('endsOn')}")
    print("      no fixture photos uploaded — the wall starts empty, real visitors fill it")

    if not args.no_host_link:
        url, _code, expires = host_api._mint_host_link(
            args.event_id, ttl_days=HOST_LINK_TTL_DAYS, recovery=False
        )
        print("\n" + "=" * 78)
        print("HOST LINK (keep private — this is a bearer credential for the console, never put it")
        print("on a public page):")
        print(f"\n  {url}\n")
        print(f"expires {expires.isoformat()}")
        print("=" * 78)

    print(f"\nPASS  {args.event_id} is ready and live.")
    print(f"      walkthrough: https://showrunner-hq.web.app/how-it-works")
    print(f"      kiosk:       https://showrunner-hq.web.app/kiosk/{args.event_id}")
    print(f"      join:        https://showrunner-hq.web.app/join/{args.event_id}")
    print("\nWhen the judging window closes, wrap it manually from the host console — this script")
    print("deliberately does not guess an end date and auto-wrap itself.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
