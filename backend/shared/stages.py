"""The one answer to "which stage is active right now" (spec 13; supersedes the inline versions).

`stageOverride || activeStage || whatever the schedule says now` — in that order, everywhere.

Before this module, the ledger fell back to the schedule while the publisher, the public endpoint
and the perception workers stopped at `activeStage` — so until a host pressed "Now: ▶" or the
director's first advance landed, the director reasoned about a stage the kiosk was not showing and
the Curator was not told about. One resolver, adopted by all five call sites, is the fix; the
precedence itself is spec 05 §2 verbatim ("host override always wins instantly").

The returned `source` ("override" | "activeStage" | "schedule" | "none") is load-bearing for the
director: an override means the host is holding the stage manually and no auto-advance may fire.

**The pin expires (multi-day fix).** `activeStage` has exactly one writer — an accepted
`PROPOSE_STAGE_ADVANCE` in `directors/story/act.py` — and nothing has ever cleared it. Because it
sits *above* the schedule in the precedence above, the first auto-advance of an event used to
disable the schedule leg permanently: every later transition then depended on the model proposing
another advance and the guardrails accepting it, and a single missed advance stranded the pointer
for the rest of the event. On a one-evening wedding that is invisible; on a five-day trip it means
the kiosk header, the Curator's per-photo stage context and the coverage ledger's whole notion of
"which stage is active" silently freeze on Tuesday's dinner while it is Thursday — and every
surface keeps looking healthy, because a stale pin is indistinguishable from a deliberate one.

So a pin is honoured only until **its own stage's window has ended plus
`STAGE_GAP_GRACE_MINUTES`** — the same cutoff `ledger.StageView.has_lapsed` already uses to stop
a lapsed stage bidding for bounty budget, reused rather than re-derived so "this stage is over"
means one thing in this system. Past that the schedule resumes, and if the schedule has nothing to
say either (overnight on a trip) the answer is honestly `none`: nothing is happening, the theme
clears, no person-coverage gap is computed for a stage nobody is at, and `director._is_idle` is
free to skip the REASON call. Three properties make this safe to adopt everywhere at once:

- **Read-side only.** No write, no migration, no new field, and no second writer of `activeStage`.
  A stale pin is left on the document and ignored, so nothing that already happened is rewritten.
- **The degradation contract holds** (spec 13 §1). A stage with no `endsAt` — every undated
  pre-spec-13 event, and any open-ended stage like a reception with no declared finish — never
  lapses, so its pin never expires and those events behave byte-for-byte as before.
- **The host still wins instantly.** `stageOverride` is checked first and is never subject to
  expiry: a host holding the stage manually holds it for as long as they like.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Mapping

from .settings import STAGE_GAP_GRACE_MINUTES


def as_dt(value: Any) -> dt.datetime | None:
    """A Firestore timestamp field as an aware UTC datetime, or None. Public: the stage-window
    shape (`startsAt`/`endsAt`, possibly absent) is read by several callers and they must all
    read it the same way."""
    if isinstance(value, dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)
    return None


def scheduled_stage_id(event: Mapping[str, Any], now: dt.datetime) -> str | None:
    """The first stage whose window contains `now`, in array order. Stages are written
    chronologically sorted (`api/host.py::save_stages`), so overlapping windows resolve to the
    earlier-starting one — the least surprising answer for a schedule a human wrote."""
    for stage in event.get("stages") or []:
        starts, ends = as_dt(stage.get("startsAt")), as_dt(stage.get("endsAt"))
        if starts is not None and ends is not None and starts <= now <= ends:
            stage_id = stage.get("stageId")
            if stage_id:
                return str(stage_id)
    return None


def pin_has_lapsed(event: Mapping[str, Any], stage_id: str, now: dt.datetime) -> bool:
    """Whether an `activeStage` pin on `stage_id` is stale — see the module docstring.

    Fails *closed on honouring the pin* in every uncertain case: a stage id that is not on this
    event at all, or one with no `endsAt`, is never called stale. The consequence of wrongly
    expiring a pin (the wall drops to the schedule mid-stage) is worse and more visible than the
    consequence of wrongly keeping one for another 90 minutes.
    """
    for stage in event.get("stages") or []:
        if str(stage.get("stageId") or "") != stage_id:
            continue
        ends = as_dt(stage.get("endsAt"))
        if ends is None:
            return False
        return now > ends + dt.timedelta(minutes=STAGE_GAP_GRACE_MINUTES)
    return False


def resolve_active(
    event: Mapping[str, Any], now: dt.datetime | None = None
) -> tuple[str | None, str]:
    """Return `(stage_id, source)`. `source` ∈ override | activeStage | schedule | none."""
    override = event.get("stageOverride")
    if override:
        return str(override), "override"
    moment = now or dt.datetime.now(dt.timezone.utc)
    active = event.get("activeStage")
    if active and not pin_has_lapsed(event, str(active), moment):
        return str(active), "activeStage"
    scheduled = scheduled_stage_id(event, moment)
    if scheduled:
        return scheduled, "schedule"
    # A lapsed pin and an empty schedule is not a failure to answer — it is the answer. Returning
    # the stale pin here would be the original bug with extra steps: the director would keep
    # computing person-coverage gaps for a stage that ended hours ago and could issue a bounty for
    # it at 3 a.m. `none` is what lets the idle predicate do its job.
    return None, "none"
