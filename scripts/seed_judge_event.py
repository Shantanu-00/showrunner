"""Seed the one `protected_demo` event the `/judge` tour lands on.

    python scripts/seed_judge_event.py
    python scripts/seed_judge_event.py --keep-others   # skip the stale-live-event sweep

This is a thin wrapper over `backend/seed.py`, deliberately. Spec 09 §5 requires the demo dataset to
arrive through the real upload path — *"a `seed.py` script uploads them through the real pipeline
(never direct Firestore writes — judges may check)"* — and `seed.py` **is** that path: signed URL →
GCS → Eventarc → Curator / Face Indexer / Guardian, exactly as a guest's phone would. A separate
judge-specific seeder would have been a second path that drifts from the graded one.

Three things this adds on top of an ordinary seed:

1. **`class: 'protected_demo'`.** Spec 11 §1.1 makes the class server-assigned and never settable
   through the public API, so a script run with ADC is the only thing that can mint it. That class is
   what exempts this event from the concurrent-live-event cap — the reason a stranger squatting
   capacity can never lock a judge out of the tour.
2. **`publicFloor: 0.0`**, set through the **ordinary** `Event.publicFloor` field that any host can
   set, not through a demo-only override. A judge's photo of their desk should still reach the wall;
   quality still owns the hero slots via the aesthetic ranking term. The `demoConfig.publicFloor`
   branch that used to do this job was deleted in S14 — see `shared/visibility.py::public_floor`.
3. **The stale-live-event sweep** HANDOFF §8b assigned here by name. From B3-S8b every `live` event
   costs a `gemini-3.7-flash` call *per tick*, so a judging-month deployment that accumulates
   abandoned `live` dev events is quietly multiplying the control plane's bill by the number of them.
   The standing rule is "a script that creates a live event owns wrapping it"; this one also wraps the
   ones earlier scripts left behind.

What it deliberately does **not** do: turn on `autoPromoteEnrollees`. That flag is gated to
`protected_demo` in the enrollment handler and it is the last judge-conditional branch in the system;
the seeded cast's tier-0/1 people demonstrate `vipWeight` just as well without putting it on a judge's
own path. `seed.py::ensure_event` leaves it `False` and says so.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(ROOT / "scripts"))

import seed as seed_module  # noqa: E402
from api import host as host_api  # noqa: E402
from schemas.event import EventClass, EventStatus  # noqa: E402
from shared import fs  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

JUDGE_EVENT_ID = "judge_demo"
JUDGE_EVENT_NAME = "Showrunner — Judge Demo Wedding"

#: A judge's photo of their desk or their badge should still reach the kiosk `just_in` strip rather
#: than reading as breakage (spec 09 §5). Real events default to 0.45 (spec 04 §2).
JUDGE_PUBLIC_FLOOR = 0.0

#: The host link handed to judges lives in the Devpost submission-instructions field, which is frozen
#: at the deadline — so it has to outlive the whole judging period (ends Oct 1) plus the two-day winner
#: verification window plus the announcement (~Oct 8). Mid-October is the honest minimum.
HOST_LINK_TTL_DAYS = 60


def sweep_stale_live_events(keep: set[str]) -> list[str]:
    """Wrap every `live`/`wrapping` event except the ones we are keeping.

    Only touches `internal_dev` and `public` classes — never another `protected_demo`, on the
    principle that this script should not be able to take down a demo event it did not create.
    """
    wrapped: list[str] = []
    now = dt.datetime.now(dt.timezone.utc)
    for snap in fs.db().collection("events").stream():
        doc = snap.to_dict() or {}
        if snap.id in keep:
            continue
        if doc.get("status") not in (EventStatus.LIVE.value, EventStatus.WRAPPING.value):
            continue
        if doc.get("class") == EventClass.PROTECTED_DEMO.value:
            continue
        snap.reference.set({"status": EventStatus.WRAPPED.value, "wrappedAt": now}, merge=True)
        wrapped.append(snap.id)
    return wrapped


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--event-id", default=JUDGE_EVENT_ID)
    ap.add_argument("--timezone", default="Asia/Kolkata")
    ap.add_argument("--api", default=None, help="api base URL (default: NEXT_PUBLIC_API_URL)")
    ap.add_argument("--regen-cast", action="store_true")
    ap.add_argument("--keep-others", action="store_true", help="skip the stale-live-event sweep")
    ap.add_argument("--no-host-link", action="store_true", help="do not mint a host link")
    args = ap.parse_args()

    print(f"Seeding the judge-mode event `{args.event_id}` (class=protected_demo)\n")

    argv = [
        "seed.py",
        "--event-id",
        args.event_id,
        "--class",
        EventClass.PROTECTED_DEMO.value,
        "--public-floor",
        str(JUDGE_PUBLIC_FLOOR),
        "--name",
        JUDGE_EVENT_NAME,
        "--timezone",
        args.timezone,
    ]
    if args.api:
        argv += ["--api", args.api]
    if args.regen_cast:
        argv.append("--regen-cast")

    saved, sys.argv = sys.argv, argv
    try:
        rc = seed_module.main()
    finally:
        sys.argv = saved
    if rc != 0:
        print("\nFAIL  the underlying seed run did not succeed — not sweeping, not minting a link")
        return rc

    if not args.keep_others:
        # Deliberately after the seed, so a failed seed never leaves the deployment with no live event
        # at all. `dev_demo` is kept because it is the development event every smoke script uses.
        wrapped = sweep_stale_live_events(keep={args.event_id, "dev_demo"})
        if wrapped:
            print(f"      swept {len(wrapped)} stale live event(s) to wrapped: {', '.join(wrapped)}")
        else:
            print("      no stale live events to sweep")

    if not args.no_host_link:
        # Reuses the console's own minting path, so the link a judge redeems is the same artifact a
        # real co-host would get — nothing bespoke, nothing that bypasses `POST /v1/claim`'s checks.
        url, _code, expires = host_api._mint_host_link(
            args.event_id, ttl_days=HOST_LINK_TTL_DAYS, recovery=False
        )
        print("\n" + "=" * 78)
        print("HOST LINK for the Devpost testing-instructions field (NOT for the /judge page —")
        print("a bearer credential on a public page lets a stranger freeze the judges' event):")
        print(f"\n  {url}\n")
        print(f"expires {expires.isoformat()}")
        print("=" * 78)

    print(f"\nPASS  {args.event_id} is seeded and live.")
    print(f"      tour:   https://showrunner-hq.web.app/judge")
    print(f"      kiosk:  https://showrunner-hq.web.app/kiosk/{args.event_id}")
    print(f"      guest:  https://showrunner-hq.web.app/join/{args.event_id}?judge=1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
