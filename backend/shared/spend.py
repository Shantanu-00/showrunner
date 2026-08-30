"""What this event has actually cost so far — derived, never stored.

`event.costSoFarUsd` is read in three places: the host console's "Pipeline Spend" KPI
(`api/host.py::console_summary`), the public-event cost ceiling the hourly sweep enforces
(`api/sweep.py`), and the frontend that renders the first of those. Until this module existed it was
written by **nobody** — a schema field with three readers and no writer. So the KPI showed `$0.00` for
the life of every event, and spec 11 §1.4's `$3` ceiling was unenforceable no matter how much the
sweep wanted to enforce it.

That is a worse failure than a missing feature. A money number that is confidently wrong is read as
"this event is free", and the one guardrail standing between a public demo URL and a credit balance was
silently disarmed.

## Why derived rather than incremented

The obvious fix is an `Increment` on the event document each time a worker spends. It is the wrong
fix, for a reason spec 09 §2 makes arithmetic: the classify and safety queues each run at **8/s**, so a
busy event would land up to 16 writes per second on one document, and Firestore's sustained
per-document write ceiling is about one per second. Every photo would contend with every other photo
for the same document, and the media path's latency would start depending on how busy the event is.

That is precisely the hot-key problem `fs.py::coverage_stage_shards_col` documents for the coverage
ledger, and the answer here is the same: don't build a hot document. Every worker *already* records its
own spend on the media document it was working on (`services/gemini.py::usage_increments` writes
`usage.tokensIn`/`tokensOut`/`tokensCached` as increments there, where there is no contention because
each worker owns a different document). So the event-level number is a **sum of numbers that already
exist**, and Firestore will compute it server-side in one aggregation query.

## Why not fold it into the coverage shards either

Tempting — they already ride the indexing transaction. But there is a trap: `bump()` receives the
*merged* post-update document from `visibility._merge`, and that helper deliberately **skips
`Increment` values** (it cannot resolve them client-side without a read). So at bump time the merged
`usage` still holds the value from before the current stage's write, and rolling it up there would
undercount every item by whichever stage happened to finish last. Silent, and off by a third.

## What this does and does not count

Counted: every Gemini token any worker spent on a media item, plus one Vision SafeSearch call per
screened item. That is the per-media perception pipeline — the thing the "Pipeline Spend" label names
and the thing that scales with guest behaviour, which is what a ceiling needs to bound.

Not counted, and deliberately: the Story Director's own per-tick reasoning, the itinerary parse
(`api/host.py` explains its own omission), and reel production (Lyria + Veo + render compute, which
`settings.py` already prices per version and the reel document already carries). Those are bounded
per-event by their own guardrails rather than by guest volume. Stated here so the number's scope is
legible instead of being inferred from whichever readers happen to exist.
"""

from __future__ import annotations

from typing import Any

from google.cloud.firestore_v1.base_query import FieldFilter

from schemas.common import MediaStatus
from shared.settings import (
    GEMINI_BLENDED_USD_PER_TOKEN,
    VISION_SAFESEARCH_USD_PER_IMAGE,
    settings,
)

from . import fs, log

#: Statuses whose media actually consumed perception budget. `awaiting_upload` and `rejected` never
#: reached a worker; `abandoned` never arrived. A duplicate is deliberately included in the *screened*
#: count being zero — it short-circuits before any paid call (`shared/pipeline.py`'s dedupe guard) — so
#: its token counters are zero and it contributes nothing without needing its own branch.
SPENT_STATUSES = (
    MediaStatus.PROCESSING.value,
    MediaStatus.INDEXED.value,
    MediaStatus.QUARANTINED.value,
)


def _sum_field(event_id: str, field: str) -> int:
    """One server-side `sum()` over one field of this event's media collection.

    **One field per query, deliberately.** Chaining the three sums into a single aggregation is the
    obvious way to write this and Firestore rejects it: a multi-field aggregation needs a composite
    index (`400 The query requires an index`), whereas each single-field sum is served by the automatic
    single-field index. Three cheap round trips beat an index that exists only to serve a KPI header —
    and `firestore.indexes.json` is a deploy artifact, so adding one would make this change require an
    index rollout before it worked.

    Aggregations bill at roughly one read per 1,000 documents scanned, which is what makes this
    affordable at five thousand photos where streaming the collection to add up a field would not be.
    """
    for group in fs.media_col(event_id).sum(field, alias="s").get():
        for result in group:
            return int(result.value or 0)
    return 0


def _sum_tokens(event_id: str) -> tuple[int, int]:
    """`(tokensIn + tokensCached, tokensOut)` across this event's media.

    Cached tokens are folded into the input total because they *are* billed — at a discount this
    blended rate does not model. Noted rather than hidden: the effect is to slightly overstate spend on
    an event with heavy prompt-cache reuse, which is the safe direction for a ceiling to err.
    """
    return (
        _sum_field(event_id, "usage.tokensIn") + _sum_field(event_id, "usage.tokensCached"),
        _sum_field(event_id, "usage.tokensOut"),
    )


def _count_screened(event_id: str) -> int:
    """How many items reached the Guardian, i.e. how many Vision calls were paid for."""
    query = fs.media_col(event_id).where(
        filter=FieldFilter("stages.safety", "in", ["done", "failed", "failed_permanent"])
    )
    for group in query.count(alias="n").get():
        for result in group:
            return int(result.value or 0)
    return 0


def compute(event_id: str) -> dict[str, Any]:
    """The event's perception spend, as a small report. Never raises.

    Returns zeros and logs on failure rather than propagating: this feeds a KPI header and a guardrail
    check, and neither should 500 because an aggregation was unavailable. **The cost ceiling reads the
    `usd` field, so a failure here reads as "no spend recorded" and the ceiling does not fire** — which
    is the correct direction to fail for a check whose action is pausing someone's live event.
    """
    try:
        tokens_in, tokens_out = _sum_tokens(event_id)
        screened = _count_screened(event_id)
    except Exception as exc:  # noqa: BLE001 - see docstring
        log.warn("spend_compute_failed", event_id=event_id, err=str(exc))
        return {"usd": 0.0, "tokensIn": 0, "tokensOut": 0, "screened": 0, "ok": False}

    usd = (tokens_in + tokens_out) * GEMINI_BLENDED_USD_PER_TOKEN
    usd += screened * VISION_SAFESEARCH_USD_PER_IMAGE
    return {
        "usd": round(usd, 4),
        "tokensIn": tokens_in,
        "tokensOut": tokens_out,
        "screened": screened,
        "ok": True,
    }


def usd(event_id: str) -> float:
    """Just the number, for the cost ceiling."""
    return float(compute(event_id).get("usd") or 0.0)


def over_ceiling(event_id: str, event: dict[str, Any]) -> tuple[bool, float, float]:
    """`(is_over, spend, ceiling)` for a `public`-class event (spec 11 §1.4).

    Only `public` events have a ceiling — the judge-mode event and the deployer's own dev events are a
    different class and never compete for a budget they were never given (spec 11 §1.1). Any other class
    reports "not over" with a ceiling of 0.0, so a caller cannot accidentally pause the demo.
    """
    from schemas.event import EventClass

    if str(event.get("class") or EventClass.PUBLIC.value) != EventClass.PUBLIC.value:
        return False, 0.0, 0.0
    ceiling = float(settings().public_event_cost_ceiling_usd)
    spend = usd(event_id)
    return spend >= ceiling, spend, ceiling
