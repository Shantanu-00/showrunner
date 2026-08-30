"""The off-topic resolver — explains why a photo's scene setting is an outlier, decides nothing.

`publisher/program.py::on_topic` already demotes an outlier photo on the kiosk — that is the whole
exposure-safe answer to "a guest uploaded a hike photo at a wedding," and it needed no gate, because a
demotion is recoverable and a wrongly-suppressed photo is not. What that demotion does not do is tell
anyone *why* a given photo scored low, and a host looking at their public gallery deserves better than
a photo that quietly sank without a reason attached to it.

This module writes that reason. One sentence, built from the same counts `on_topic` reads, on the
media document itself — `offTopicNote`, the field `schemas/moderation.py::ReviewQueueItem` and
`ReviewPanel.tsx` already render whenever a note happens to be present. **No new queue, no new
endpoint**: if the item is also `host_review` for an unrelated reason, the reviewing host sees the
note alongside it; otherwise the note sits on the document as an honest, inert fact, exactly the
posture `directors/story/taste.py` already accepts for `tasteMemo` before spec 07's ranking existed to
read it.

**Deterministic, not model-written.** The plan that produced this module first sketched a cheap-model
prose pass. On reflection that is the wrong tool: the sentence is arithmetic translated to English —
"N of M photos here are X; this one is Y" — and a model asked to phrase arithmetic can still get the
arithmetic wrong. A host reading this sentence to decide whether to trust the system is the one place
in this feature where a hallucination risk buys nothing a template does not already provide for free.

**What it does not do**, each for a reason already established elsewhere in this build:

- It does not gate exposure. `visibility` has exactly one writer and six inputs, and this is not one
  of them.
- It does not blend in the host's Memory Bank preferences. The design considered it — "keep the drone
  shots off the wall" is legitimate context for an explanation — but doing that well needs matching
  free text against a photo's attributes, which is a fuzzy problem this module has no reason to solve
  today. Left for later rather than done badly now.
- It does not introduce a priority-sorted queue. The natural-sounding "rarer photos sort first" was in
  the original design, but the only queue that exists (`api/moderation.py`'s review-queue endpoint) is
  keyed on the Guardian's verdict, and an off-topic photo is not usually a Guardian concern — sorting
  that queue by a number most of its rows do not have would be a feature with no real audience.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fastapi.concurrency import run_in_threadpool
from google.cloud.firestore_v1.base_query import FieldFilter

from schemas.common import UNINFORMATIVE_SETTINGS, MediaStatus, Visibility
from shared import coverage, fs, log
from shared.settings import WORLD_MIN_CORPUS, WORLD_ONTOPIC_RARE_SHARE

#: Bounded per tick, same shape and same reasoning as `validate.py::MAX_PER_TICK`: a tick that spent
#: its whole budget writing explanatory notes would miss its own cadence, and the backlog is still
#: there next tick. NOT spec-pinned — no spec mentions this mechanism at all (HANDOFF §9).
MAX_PER_TICK = 8

#: Page size for the Python-side filter below. A handful of documents carry no `offTopicCheckedAt` at
#: any moment on a healthy event, so a composite index for this would serve almost nothing — same
#: trade `validate.py::_pending` documents for the identical shape of query.
SCAN_LIMIT = 60


@dataclass
class Resolved:
    noted: list[str] = field(default_factory=list)
    checked: int = 0


def _pending(event_id: str) -> list[dict[str, Any]]:
    """Public, indexed media with a real scene setting that no tick has resolved yet.

    Restricted to `visibility == 'public'`: a `pool`/`self` item is not on any public surface, so
    "why is this on the wall" does not apply to it, and there is no urgency to spend a write explaining
    a photo nobody but its owner and its subjects can see.
    """
    query = (
        fs.media_col(event_id)
        .where(filter=FieldFilter("visibility", "==", Visibility.PUBLIC.value))
        .where(filter=FieldFilter("status", "==", MediaStatus.INDEXED.value))
        .order_by("uploadedAt")
        .limit(SCAN_LIMIT)
    )
    found: list[dict[str, Any]] = []
    try:
        for snap in query.stream():
            doc = snap.to_dict() or {}
            if doc.get("offTopicCheckedAt"):
                continue
            setting = (doc.get("curator") or {}).get("sceneSetting")
            if not setting or setting in UNINFORMATIVE_SETTINGS:
                continue
            doc.setdefault("mediaId", snap.id)
            found.append(doc)
    except Exception as exc:  # noqa: BLE001 - a resolution pass must not fail the tick
        log.warn("resolve_pending_query_failed", event_id=event_id, err=str(exc))
    return found


def _note(setting: str, share: float, totals: dict[str, int], informative_total: int) -> str:
    """The one sentence, built from counts alone. See the module docstring for why this is a
    template rather than a model call."""
    dominant = max(
        (t for t in totals if t not in UNINFORMATIVE_SETTINGS),
        key=lambda t: totals[t],
        default=None,
    )
    count = totals.get(setting, 0)
    pct = round(share * 100)
    if dominant and dominant != setting:
        dom_pct = round((totals.get(dominant, 0) / informative_total) * 100) if informative_total else 0
        return (
            f"{dom_pct}% of this event's photos are {dominant.replace('_', ' ')}; this one is "
            f"{setting.replace('_', ' ')}, seen in only {count} of {informative_total} ({pct}%)."
        )
    return (
        f"This is one of {count} {setting.replace('_', ' ')} photos out of {informative_total} "
        f"at this event ({pct}%)."
    )


def _mark(event_id: str, media_id: str, note: str | None) -> None:
    updates: dict[str, Any] = {"offTopicCheckedAt": fs.SERVER_TIMESTAMP}
    if note:
        updates["offTopicNote"] = note[:200]
    try:
        fs.media_ref(event_id, media_id).update(updates)
    except Exception as exc:  # noqa: BLE001 - one write failing must not stop the rest of the pass
        log.warn("resolve_mark_failed", event_id=event_id, media_id=media_id, err=str(exc))


def resolve_pending(event_id: str) -> Resolved:
    """Called from the tick (`api/internal.py::_do_work`). Bounded, best-effort, never raises.

    Reads the coverage shards the director's own LEDGER step already fetched this tick where
    possible — but this function is standalone and re-reads them itself, because unlike `world.py`
    (which is handed the shards to avoid a second aggregation on every tick) this one only ever
    touches a handful of documents and the extra read is cheap relative to the writes it makes.
    """
    result = Resolved()
    try:
        shards = coverage.read(event_id)
    except Exception as exc:  # noqa: BLE001
        log.warn("resolve_coverage_read_failed", event_id=event_id, err=str(exc))
        return result

    totals = coverage.scene_totals(shards)
    informative_total = sum(n for tag, n in totals.items() if tag not in UNINFORMATIVE_SETTINGS)
    if informative_total < WORLD_MIN_CORPUS:
        # Not enough evidence for a share to mean anything — same gate `program.py::on_topic` uses,
        # so a note is never written about a photo the ranking itself has no opinion on yet.
        return result

    for doc in _pending(event_id)[:MAX_PER_TICK]:
        media_id = str(doc.get("mediaId") or "")
        setting = str((doc.get("curator") or {}).get("sceneSetting") or "")
        share = totals.get(setting, 0) / informative_total if informative_total else 0.0
        note = _note(setting, share, totals, informative_total) if share < WORLD_ONTOPIC_RARE_SHARE else None
        _mark(event_id, media_id, note)
        result.checked += 1
        if note:
            result.noted.append(media_id)

    if result.checked:
        log.line("resolve", event_id=event_id, checked=result.checked, noted=len(result.noted))
    return result


async def run_pending(event_id: str) -> Resolved:
    """Async wrapper, called beside `taste.run_pending`/`world.run_if_due` in `_do_work`. Never raises."""
    try:
        return await run_in_threadpool(resolve_pending, event_id)
    except Exception as exc:  # noqa: BLE001 - must not fail the tick
        log.warn("resolve_failed", event_id=event_id, err=str(exc))
        return Resolved()
