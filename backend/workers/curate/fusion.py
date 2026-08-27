"""Stage fusion — spec 03 §5.1's deterministic post-LLM step.

`stagePosterior = argmax(visual_score[stage] × temporal_prior[stage])`.

The Curator is shown the photo and nothing about *when* it was taken; this module supplies the
timing. Keeping the two signals separate is what makes the acceptance test work — "Haldi photo
uploaded 6 h late classifies as Haldi" — because the prior is indexed by EXIF `capturedAt`, not
upload time, and because a wrong prior can be overruled by strong visual evidence rather than
silently deciding the answer.

It is also what makes disagreement *measurable*: the raw `visual` distribution is stored alongside
the fused posterior, and the Story Director reads a confident visual answer that fights the
schedule as a stage-drift signal (spec 05 §4) — the ceremony is running late, and the system can
say so instead of mislabelling an hour of photos.

Three cases the prior has to get right, in order of how often they actually happen:

1. **No EXIF** (WhatsApp forwards, screenshots — very common): the prior is flattened to 0.5
   everywhere. Not 1.0, not 0.15: a flat prior contributes no ordering information at all, so the
   argmax falls through to pure visual evidence. Using `uploadedAt` here would confidently label
   every forwarded photo with whatever is happening right now.
2. **Photo inside a stage's window**: prior 1.0, and a ±30 min ramp on each edge so a photo taken
   four minutes before the Sangeet was scheduled to start is not treated like one from breakfast.
3. **Unscheduled stage**: 0.5, not 0.15. A stage the host never gave times to has no temporal
   opinion, and penalising it would quietly make an unscheduled stage unreachable.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from shared.settings import (
    STAGE_PRIOR_FLAT,
    STAGE_PRIOR_IN_WINDOW,
    STAGE_PRIOR_OUT_OF_WINDOW,
    STAGE_PRIOR_RAMP_MINUTES,
)


def temporal_prior(captured_at: dt.datetime | None, stage: dict[str, Any]) -> float:
    """The prior for one stage at one capture time.

    A stage with only `startsAt` (or only `endsAt`) is treated as open-ended on the missing side,
    which is how an open-bar reception with no declared finish actually behaves.
    """
    if captured_at is None:
        return STAGE_PRIOR_FLAT

    starts, ends = _as_utc(stage.get("startsAt")), _as_utc(stage.get("endsAt"))
    if starts is None and ends is None:
        return STAGE_PRIOR_FLAT

    if starts is not None and captured_at < starts:
        return _ramp((starts - captured_at).total_seconds() / 60.0)
    if ends is not None and captured_at > ends:
        return _ramp((captured_at - ends).total_seconds() / 60.0)
    return STAGE_PRIOR_IN_WINDOW


def _ramp(minutes_outside: float) -> float:
    """Linear decay from in-window to out-of-window across the ramp; flat beyond it."""
    if minutes_outside >= STAGE_PRIOR_RAMP_MINUTES:
        return STAGE_PRIOR_OUT_OF_WINDOW
    fraction = minutes_outside / STAGE_PRIOR_RAMP_MINUTES
    return STAGE_PRIOR_IN_WINDOW - fraction * (STAGE_PRIOR_IN_WINDOW - STAGE_PRIOR_OUT_OF_WINDOW)


def fuse(
    visual: dict[str, float],
    stages: list[dict[str, Any]],
    captured_at: dt.datetime | None,
    *,
    exif_missing: bool = False,
) -> tuple[str | None, dict[str, float]]:
    """Return `(stageId, stagePosterior)` — the argmax and the normalised product distribution.

    The posterior is normalised so it reads as a distribution: downstream code compares its top
    value against the raw visual top value to measure confidence, and that comparison is only
    meaningful on a common scale. Ties resolve to the earlier stage in the event's own ordering,
    which keeps the answer stable across re-runs rather than depending on dict iteration order.
    """
    if not stages:
        return None, {}

    # `exifMissing` and a null `capturedAt` are the same situation — intake sets the flag and then
    # falls back to upload time in the same field, so the flag is the authoritative signal.
    index_time = None if exif_missing else captured_at

    products: dict[str, float] = {}
    for stage in stages:
        stage_id = str(stage.get("stageId") or "")
        if not stage_id:
            continue
        score = float(visual.get(stage_id) or 0.0)
        products[stage_id] = score * temporal_prior(index_time, stage)

    total = sum(products.values())
    if total <= 0.0:
        # The model gave every scheduled stage a zero — no visual evidence for any of them. An
        # honest "don't know" beats crowning whichever stage the clock happens to favour: the
        # Story Director treats a null stageId as uncovered rather than as a wrong label.
        return None, {stage_id: 0.0 for stage_id in products}

    posterior = {stage_id: value / total for stage_id, value in products.items()}
    order = {stage_id: position for position, stage_id in enumerate(products)}
    best = max(posterior, key=lambda stage_id: (posterior[stage_id], -order[stage_id]))
    return best, posterior


def _as_utc(value: Any) -> dt.datetime | None:
    """Firestore hands back tz-aware datetimes; a hand-seeded fixture might not."""
    if not isinstance(value, dt.datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(dt.timezone.utc)
