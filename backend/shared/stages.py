"""The one answer to "which stage is active right now" (spec 13; supersedes the inline versions).

`stageOverride || activeStage || whatever the schedule says now` — in that order, everywhere.

Before this module, the ledger fell back to the schedule while the publisher, the public endpoint
and the perception workers stopped at `activeStage` — so until a host pressed "Now: ▶" or the
director's first advance landed, the director reasoned about a stage the kiosk was not showing and
the Curator was not told about. One resolver, adopted by all five call sites, is the fix; the
precedence itself is spec 05 §2 verbatim ("host override always wins instantly").

The returned `source` ("override" | "activeStage" | "schedule" | "none") is load-bearing for the
director: an override means the host is holding the stage manually and no auto-advance may fire.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Mapping


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


def resolve_active(
    event: Mapping[str, Any], now: dt.datetime | None = None
) -> tuple[str | None, str]:
    """Return `(stage_id, source)`. `source` ∈ override | activeStage | schedule | none."""
    override = event.get("stageOverride")
    if override:
        return str(override), "override"
    active = event.get("activeStage")
    if active:
        return str(active), "activeStage"
    moment = now or dt.datetime.now(dt.timezone.utc)
    scheduled = scheduled_stage_id(event, moment)
    if scheduled:
        return scheduled, "schedule"
    return None, "none"
