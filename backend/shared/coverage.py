"""The coverage ledger — spec 05 §1's LEDGER inputs, kept as a materialized view in Firestore.

Spec 05 §1 says the ledger step "aggregates Firestore into `ledger/coverage`". It does not say how,
and the how is a real decision (EXECUTION-PLAN §7d): aggregating on read is O(photos), which is tens
of seconds at five thousand photos — and a 30-second demo tick that takes forty seconds to think
overlaps itself. So the counters are incremented instead, exactly once per item, **inside the
transaction that already flips `status='indexed'`** (`shared/pipeline.py`). Two consequences worth
being explicit about:

- **Exactly-once by construction, not by convention.** `pipeline._derive_status` returns an update
  only on the transition into `indexed` and never again — a replayed stage, a duplicate Cloud Tasks
  delivery and a host re-review all find the item already `indexed` and bump nothing. There is no
  separate idempotency key because the transition itself is the key.
- **Write-only, and therefore contention-free.** Every field is either an `Increment` or a
  last-writer-wins timestamp, so this module never reads a shard inside the media transaction. That
  is what keeps the media path's latency independent of how busy a stage is. It also costs one thing,
  named here rather than hidden: there is no `bestAestheticScore`, because a maximum cannot be
  maintained without a read. `highlightCount` (how many of this stage's photos cleared the 0.75
  highlight bar) and `aestheticSum` (mean, on read) answer the same question the director actually
  asks — "do we have *good* photos of this, or only photos" — without putting a read-modify-write on
  the hot path. Recorded in HANDOFF §9 as an S8b choice no spec pinned.

The counters count **evidence that a photograph exists**, not exposure. A Ring-0 photo of the bride's
mother still proves she was photographed during the Haldi, so the director should not issue a bounty
for a gap that is not there; `publicCount` is tracked separately for the questions that are actually
about the wall. Nothing here is ever used to decide visibility.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

from google.cloud import firestore

from schemas.common import UNINFORMATIVE_SETTINGS, SceneSetting, Visibility

from . import fs, log

#: Shard id for items whose stage could not be attributed (`curator.stageId` is null — a photo with
#: no visual evidence for any scheduled stage, or an event with no schedule yet). A real document id
#: rather than a skipped write: "nine photos arrived and we cannot place any of them" is a signal.
UNSTAGED = "_unstaged"

#: The Curator's own highlight bar (`workers/curate/agent.py`: `aestheticScore >= 0.75 AND shows a
#: moment`). Reused rather than re-thresholded so "a good photo" means one thing in this system.
_HIGHLIGHT_FIELD = "curator"

#: The people-count histogram's buckets (spec 13 §5), keyed by their inclusive lower bound. Coarse
#: on purpose: `peopleCountEstimate` drifts ±1 on group shots (the B2-S4 resolution finding), so
#: buckets that a ±1 error cannot cross at the sizes that matter are what keep the histogram
#: honest. Order matters only to `bucket_for`.
PEOPLE_BUCKET_FLOORS: dict[str, int] = {"p1": 1, "p2_3": 2, "p4_6": 4, "p7_12": 7, "p13up": 13}


def bucket_for(people_count: int) -> str | None:
    """The histogram bucket for one frame's estimated head-count. None for zero/no estimate —
    a landscape is not a 0-person group shot, it is not a people observation at all."""
    if people_count <= 0:
        return None
    chosen = None
    for bucket, floor in PEOPLE_BUCKET_FLOORS.items():
        if people_count >= floor:
            chosen = bucket
    return chosen


@dataclass
class StageCoverage:
    """One stage's aggregate, as the ledger step sees it."""

    stage_id: str
    photo_count: int = 0
    public_count: int = 0
    highlight_count: int = 0
    aesthetic_sum: float = 0.0
    last_captured_at: dt.datetime | None = None
    #: `momentId → count`, from the Curator's `momentTags` (which reuse `requiredMoments` ids
    #: verbatim when they match — see the Curator instruction).
    moments: dict[str, int] = field(default_factory=dict)
    #: `personId → appearances`. Only claimed people appear here; unclaimed face clusters are not a
    #: coverage question, they are an identity question.
    people: dict[str, int] = field(default_factory=dict)
    #: `sceneSetting → count` (spec 03 §5.1). The observed physical setting of this stage's photos —
    #: the world model's hard layer, and the only thing any decision is allowed to read.
    scenes: dict[str, int] = field(default_factory=dict)
    #: People-count histogram from `curator.peopleCountEstimate` (spec 13 §5): bucket → count.
    #: A histogram rather than a stored `groupShotCount` because the group threshold depends on
    #: `expectedParticipants`, which the host can edit after the fact — thresholds apply at read,
    #: and this module's own rule ("write-only, no read-modify-write on the hot path") means a
    #: max could not be maintained here anyway.
    people_buckets: dict[str, int] = field(default_factory=dict)

    @property
    def mean_aesthetic(self) -> float:
        return (self.aesthetic_sum / self.photo_count) if self.photo_count else 0.0

    def frames_with_at_least(self, people: int) -> int:
        """How many of this stage's photos hold at least `people` faces-worth of humans, by the
        Curator's estimate. Bucket lower bounds are what make this answerable from a histogram:
        a `p4_6` frame provably holds ≥4, so a threshold of 4 counts it and a threshold of 5
        does not — the conservative direction for a gap detector (understate coverage, never
        overstate it)."""
        return sum(
            count
            for bucket, count in self.people_buckets.items()
            if PEOPLE_BUCKET_FLOORS.get(bucket, 0) >= people and count > 0
        )

    @property
    def dominant_scene(self) -> str | None:
        """The most-observed setting for this stage, ignoring the two that carry no information.

        `closeup_detail` and `unknown` are excluded because a stage whose photos are mostly ring shots
        has not told us where it happened — returning `closeup_detail` as its "setting" would be
        reporting the absence of evidence as evidence, which is exactly the mistake
        `UNINFORMATIVE_SETTINGS` exists to prevent.
        """
        informative = {
            tag: n for tag, n in self.scenes.items() if tag not in UNINFORMATIVE_SETTINGS and n > 0
        }
        if not informative:
            return None
        return max(informative, key=lambda tag: (informative[tag], tag))


def bump(
    transaction: firestore.Transaction,
    event_id: str,
    media: dict[str, Any],
) -> str | None:
    """Increment this item's stage shard. Called from inside the indexing transaction.

    Returns the shard id it touched, for the caller's log line. Never reads, never raises for a
    malformed document: a coverage counter that could fail an indexing transaction would be a ledger
    that takes photos hostage.
    """
    try:
        curator = media.get(_HIGHLIGHT_FIELD) or {}
        stage_id = str(curator.get("stageId") or UNSTAGED)
        aesthetic = float(curator.get("aestheticScore") or 0.0)

        updates: dict[str, Any] = {
            "stageId": stage_id,
            "photoCount": firestore.Increment(1),
            "aestheticSum": firestore.Increment(aesthetic),
            "updatedAt": fs.SERVER_TIMESTAMP,
        }
        if curator.get("isHighlight"):
            updates["highlightCount"] = firestore.Increment(1)
        if media.get("visibility") == Visibility.PUBLIC.value:
            updates["publicCount"] = firestore.Increment(1)

        captured = media.get("capturedAt")
        if isinstance(captured, dt.datetime):
            # Last writer wins. Approximately-latest is all the director asks of this field ("when
            # did we last see anything from this stage"), and the alternative is a read.
            updates["lastCapturedAt"] = captured

        moments = {
            str(tag): firestore.Increment(1)
            for tag in (curator.get("momentTags") or [])
            if tag and _safe_key(str(tag))
        }
        if moments:
            updates["moments"] = moments

        # The world model's hard layer (spec 03 §5.1's `sceneSetting`). One more `Increment` on a map
        # that already exists in shape, which is the whole reason it lives here rather than in a
        # document of its own: it inherits this transaction's exactly-once property for free, because
        # `pipeline._derive_status` returns an update only on the transition into `indexed` and never
        # again. A separate `ledger/worldModel` counter would need either a second write inside this
        # transaction or one outside it, and the second is a distribution that can drift from the media
        # it claims to describe.
        #
        # Per-stage rather than event-wide, and that is the point rather than a bonus: an event is a
        # sequence of settings (a wedding starts indoors and moves outdoors for the baraat), so an
        # event-wide count would read every stage transition as an anomaly. `scene_totals` below sums
        # the shards when the event-wide view is genuinely what is wanted, at no extra read.
        scene = str(curator.get("sceneSetting") or SceneSetting.UNKNOWN.value)
        if _safe_key(scene):
            updates["scenes"] = {scene: firestore.Increment(1)}

        people = {
            str(person): firestore.Increment(1)
            for person in (media.get("albumOf") or [])
            if person and _safe_key(str(person))
        }
        if people:
            updates["people"] = people

        # The people-count histogram (spec 13 §5) — same exactly-once property as every other
        # counter here, inherited from the indexing transition for free.
        bucket = bucket_for(int(curator.get("peopleCountEstimate") or 0))
        if bucket:
            updates["peopleBuckets"] = {bucket: firestore.Increment(1)}

        transaction.set(fs.coverage_stage_shard_ref(event_id, stage_id), updates, merge=True)
        return stage_id
    except Exception as exc:  # noqa: BLE001 - see docstring: never fail the indexing transaction
        log.warn("coverage_bump_failed", event_id=event_id, err=str(exc))
        return None


def _safe_key(key: str) -> bool:
    """Firestore map keys cannot be empty or contain a dot (it would read as a field path)."""
    return bool(key) and "." not in key and "/" not in key and len(key) <= 128


def read(event_id: str) -> dict[str, StageCoverage]:
    """Every stage shard, keyed by stageId. A handful of small documents, whatever the event size."""
    out: dict[str, StageCoverage] = {}
    for snap in fs.coverage_stage_shards_col(event_id).stream():
        doc = snap.to_dict() or {}
        out[snap.id] = StageCoverage(
            stage_id=snap.id,
            photo_count=int(doc.get("photoCount") or 0),
            public_count=int(doc.get("publicCount") or 0),
            highlight_count=int(doc.get("highlightCount") or 0),
            aesthetic_sum=float(doc.get("aestheticSum") or 0.0),
            last_captured_at=doc.get("lastCapturedAt")
            if isinstance(doc.get("lastCapturedAt"), dt.datetime)
            else None,
            moments={str(k): int(v or 0) for k, v in (doc.get("moments") or {}).items()},
            people={str(k): int(v or 0) for k, v in (doc.get("people") or {}).items()},
            scenes={str(k): int(v or 0) for k, v in (doc.get("scenes") or {}).items()},
            people_buckets={
                str(k): int(v or 0) for k, v in (doc.get("peopleBuckets") or {}).items()
            },
        )
    return out


def scene_totals(shards: dict[str, StageCoverage]) -> dict[str, int]:
    """Event-wide `sceneSetting → count`, summed across the shards `read()` already fetched.

    A free derivation rather than a stored counter, which is the point: a second copy of this number
    would be a second thing that can disagree with the media it describes. Callers that want the
    per-stage view read `shards[stageId].scenes` directly — and should prefer it, since an event's
    settings legitimately change as it moves between stages.
    """
    totals: dict[str, int] = {}
    for shard in shards.values():
        for tag, count in shard.scenes.items():
            totals[tag] = totals.get(tag, 0) + count
    return totals


def clear(event_id: str) -> int:
    """Delete every shard — used by `make demo-reset`, which wipes the media the counters counted.

    A reset that cleared the photos but not the ledger would leave the director reasoning about
    coverage that no longer exists, which is precisely the class of bug the hashes register produced
    in B2-S7 (HANDOFF §8).
    """
    deleted = 0
    for snap in fs.coverage_stage_shards_col(event_id).stream():
        snap.reference.delete()
        deleted += 1
    return deleted
