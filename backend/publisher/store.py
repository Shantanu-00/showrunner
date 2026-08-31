"""Everything the publisher reads and the one thing it writes.

Kept apart from `program.py` (which decides) and `runner.py` (which schedules) so that the queries
this service depends on are all visible in one place — they are the same queries the kiosk client
runs, against the same composite indexes (spec 09 §3), which is not a coincidence: a publisher that
ranked over a different set than the client can read would put mediaIds on the wall that the wall
cannot fetch.

The write is a transaction, even though the per-event lease already guarantees a single writer. Two
reasons: it returns the revision number it just assigned without a second read, and it lets the
fingerprint check and the increment be one step, so two overlapping nudges cannot both decide the
program changed and bump the revision twice.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any, Iterable

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from schemas.common import UNINFORMATIVE_SETTINGS, MediaStatus, Visibility
from schemas.event import EventStatus
from shared import coverage, fs
from shared.settings import DEFAULT_TIER, KIOSK_CANDIDATE_LIMIT
from shared.stages import as_dt, resolve_active

from . import program

#: How many of the most recently *uploaded* public items to pull, on top of the capturedAt-ordered
#: hero pool. A forwarded photo can be public and brand new while its EXIF capture time is a year
#: old, which would bury it below the hero query's limit — and that item is precisely the one the
#: "your photo is on the wall" guarantee is about.
RECENT_LIMIT = 20

#: Trimmed on every write. Long enough that a reel cannot be premiered twice by an instance restart,
#: short enough that the playlist document stays small.
PREMIERE_MEMORY = 20

#: Added to a reel's own `durationSec` to get how long it keeps the wall. Covers `ReelSlot`'s title
#: card, the crossfade in and the end card — a premiere that vanished on the last frame would still be
#: a premiere the room never saw the end of.
PREMIERE_GRACE_SEC = 8

#: What a premiere holds the wall for when its document carries no `durationSec` (a pre-`durationSec`
#: reel, or one whose render never reported). Longer than any reel this pipeline cuts, deliberately:
#: over-holding costs one stale slide, under-holding costs the whole feature.
PREMIERE_FALLBACK_HOLD_SEC = 60.0

#: Statuses the publisher maintains a playlist for. `wrapping` is included because the finale is the
#: most important thing the wall ever shows (spec 08 §2); `paused` is not, so the wall keeps its last
#: program instead of going blank when a host hits pause.
PUBLISHED_STATUSES = (EventStatus.LIVE.value, EventStatus.WRAPPING.value)


@dataclass
class EventContext:
    """The non-media inputs to one program build."""

    event: dict[str, Any]
    active_stage_id: str | None
    previous_stage_id: str | None
    theme: str | None


def live_query() -> firestore.Query:
    """Every event whose wall the publisher is responsible for. One query, no per-event infra —
    an event going live is a status flip and nothing else (spec 08 §2)."""
    return fs.db().collection("events").where(
        filter=FieldFilter("status", "in", list(PUBLISHED_STATUSES))
    )


def live_event_ids() -> list[str]:
    return [snap.id for snap in live_query().stream()]


def event_context(event_id: str, event: dict[str, Any] | None = None) -> EventContext | None:
    """Resolve the active stage, its predecessor and the stage's kiosk theme.

    Active = `shared/stages.py::resolve_active` — override, then `activeStage`, then the schedule
    (spec 05 §2 + spec 13). The schedule leg is what puts "Now" on the wall of an event whose host
    never touched the "Now: ▶" button and whose director has not advanced yet; the big button still
    always wins instantly.

    Previous = the latest-starting stage strictly *before* the active one **by time**, not by array
    position: `save_stages` sorts what it writes, but a mid-event edit history is exactly the kind
    of thing "sorted at the writer" cannot retroactively promise, and the ×0.4 ranking grace this
    feeds (spec 04 §4) should follow the timeline, not the array. Undated stages fall back to array
    order, which for them is the only order there is.
    """
    doc = event if event is not None else fs.get_event(event_id)
    if doc is None:
        return None
    stages: list[dict[str, Any]] = list(doc.get("stages") or [])
    active, _source = resolve_active(doc)
    previous = None
    if active:
        current = next((s for s in stages if str(s.get("stageId")) == active), None)
        current_start = as_dt(current.get("startsAt")) if current else None
        if current_start is not None:
            earlier = [
                (as_dt(s.get("startsAt")), str(s.get("stageId")))
                for s in stages
                if s.get("stageId") and str(s.get("stageId")) != active
            ]
            dated = [(at, sid) for at, sid in earlier if at is not None and at < current_start]
            if dated:
                previous = max(dated)[1]
        else:
            ids = [str(s.get("stageId")) for s in stages if s.get("stageId")]
            if active in ids:
                index = ids.index(active)
                previous = ids[index - 1] if index > 0 else None
    theme = next(
        (s.get("theme") for s in stages if s.get("stageId") == active and s.get("theme")), None
    )
    return EventContext(doc, active, previous, theme)


# ---------------------------------------------------------------- candidates


def public_query(event_id: str) -> firestore.Query:
    """The hero pool: spec 04 §2's public gate, capturedAt desc, spec 04 §3's `limit(60)`."""
    return (
        fs.media_col(event_id)
        .where(filter=FieldFilter("visibility", "==", Visibility.PUBLIC.value))
        .where(filter=FieldFilter("status", "==", MediaStatus.INDEXED.value))
        .order_by("capturedAt", direction=firestore.Query.DESCENDING)
        .limit(KIOSK_CANDIDATE_LIMIT)
    )


def recent_query(event_id: str) -> firestore.Query:
    """The same gate ordered by *upload* recency — the `just_in` index (spec 04 §4)."""
    return (
        fs.media_col(event_id)
        .where(filter=FieldFilter("visibility", "==", Visibility.PUBLIC.value))
        .where(filter=FieldFilter("status", "==", MediaStatus.INDEXED.value))
        .order_by("uploadedAt", direction=firestore.Query.DESCENDING)
        .limit(RECENT_LIMIT)
    )


def bounty_query(event_id: str) -> firestore.Query:
    return fs.event_ref(event_id).collection("bounties").where(
        filter=FieldFilter("status", "in", ["active", "escalated"])
    )


def reel_query(event_id: str) -> firestore.Query:
    return fs.event_ref(event_id).collection("reels").where(
        filter=FieldFilter("visibility", "==", Visibility.PUBLIC.value)
    )


def tier_map(event_id: str) -> dict[str, int]:
    """`personId → tier`, read fresh per build. Dozens of documents at most, and it is the input to
    a *deterministic* ranking multiplier (spec 11 §3.3), so a stale cache here would silently mean a
    promoted person stops being prominent — the exact failure the host would report as a bug."""
    tiers: dict[str, int] = {}
    for snap in fs.people_col(event_id).stream():
        doc = snap.to_dict() or {}
        try:
            tiers[snap.id] = int(doc.get("tier", DEFAULT_TIER))
        except (TypeError, ValueError):
            tiers[snap.id] = DEFAULT_TIER
    return tiers


def to_candidate(doc: dict[str, Any], tiers: dict[str, int]) -> program.Candidate:
    """One media document as the ranker sees it. Reads only already-stored fields — the publisher
    computes ranking, never perception."""
    curator = doc.get("curator") or {}
    faces = [f for f in (doc.get("faces") or []) if isinstance(f, dict)]

    keys: set[str] = set()
    for face in faces:
        cluster = face.get("clusterId") or face.get("personId")
        if cluster:
            keys.add(f"face:{cluster}")
    for tag in curator.get("momentTags") or []:
        if tag:
            keys.add(f"moment:{tag}")

    in_frame = [tiers.get(str(f.get("personId"))) for f in faces if f.get("personId")]
    return program.Candidate(
        media_id=str(doc.get("mediaId") or ""),
        aesthetic=float(curator.get("aestheticScore") or 0.0),
        captured_at=_as_datetime(doc.get("capturedAt")),
        uploaded_at=_as_datetime(doc.get("uploadedAt")),
        stage_id=curator.get("stageId"),
        moment_tags=tuple(str(t) for t in (curator.get("momentTags") or [])),
        dedupe_keys=frozenset(keys),
        vip_weight=program.vip_weight([t for t in in_frame if t is not None]),
        scene_setting=curator.get("sceneSetting"),
    )


def candidates(event_id: str) -> list[program.Candidate]:
    """The hero pool ∪ the recently-uploaded pool, de-duplicated by mediaId."""
    tiers = tier_map(event_id)
    docs: dict[str, dict[str, Any]] = {}
    for query in (public_query(event_id), recent_query(event_id)):
        for snap in query.stream():
            doc = snap.to_dict() or {}
            doc.setdefault("mediaId", snap.id)
            docs[snap.id] = doc
    return [to_candidate(doc, tiers) for doc in docs.values()]


def scene_context(event_id: str, event: dict[str, Any]) -> program.SceneContext:
    """The `onTopic` term's input, built from the coverage shards the director's own LEDGER step
    also reads — no new collection, no new I/O shape.

    **Gated on `access.mode == 'open' and access.kioskPublic`.** The just-landed event-access
    boundary changes what "public" means: on an invite-only or kiosk-private event, Ring 2 already
    resolves to *the people in this event*, not the internet, so an off-topic photo there is a
    non-problem — the audience is exactly the people who took the hike. That narrowing conveniently
    excludes the small, low-corpus events where the statistics have no reliable signal anyway
    (`program.py::on_topic`'s `WORLD_MIN_CORPUS` gate would mostly no-op for them regardless).
    """
    access = event.get("access") or {}
    mode = str(access.get("mode") or "open")
    if mode != "open" or not bool(access.get("kioskPublic", True)):
        return program.SceneContext(enabled=False)

    shards = coverage.read(event_id)
    totals = coverage.scene_totals(shards)
    informative_total = sum(n for tag, n in totals.items() if tag not in UNINFORMATIVE_SETTINGS)
    expected_by_stage = {
        str(stage.get("stageId")): str(stage.get("expectedSetting"))
        for stage in (event.get("stages") or [])
        if stage.get("stageId") and stage.get("expectedSetting")
    }
    return program.SceneContext(
        totals=totals,
        informative_total=informative_total,
        expected_by_stage=expected_by_stage,
        enabled=True,
    )


def takeover_bounty(event_id: str, *, now: dt.datetime | None = None) -> str | None:
    """The bounty that has earned the whole screen (spec 04 §4's `bounty_call`).

    Reads the documents; `program.pick_takeover` decides. The split is the same one the rest of this
    pair keeps — `store.py` does I/O, `program.py` is pure and therefore checkable by
    `scripts/smoke_autonomy.py --program-only` with no network. The freshness rule that lives there is
    what stops an unfulfilled bounty from owning the wall indefinitely on a quiet event.
    """
    rows: list[dict[str, Any]] = []
    for snap in bounty_query(event_id).stream():
        doc = snap.to_dict() or {}
        rows.append(
            {
                "bountyId": snap.id,
                "status": doc.get("status"),
                "kioskTakeover": doc.get("kioskTakeover"),
                "escalatedAt": _as_datetime(doc.get("escalatedAt")),
                "createdAt": _as_datetime(doc.get("createdAt")),
            }
        )
    return program.pick_takeover(rows, now or dt.datetime.now(dt.timezone.utc))


def premiere_reel(
    event_id: str,
    already_premiered: Iterable[str],
    *,
    holding_id: str | None = None,
    holding_since: Any = None,
    now: dt.datetime | None = None,
) -> str | None:
    """The reel that should be leading the wall: a new premiere, **or the one still playing**.

    `already_premiered` used to end the story — a reel entered the history on the write that first put
    it on screen, so the *next* rebuild dropped it. Rebuilds are driven by the director's nudge and a
    5-minute fallback, so in practice the next one landed within seconds: measured on `judge_demo`, a
    20.4-second film led revision 157 and was gone from revision 158 **3.4 seconds later**. A premiere
    that could not survive its own runtime was not a feature, and every reel this project had rendered
    was pulled off the wall mid-play.

    So the history answers "has this had its turn?" and the hold answers "is its turn over?".

    The hold is measured from `premiereStartedAt` — **when the wall started showing it** — and not from
    the reel's own `publishedAt`. They are usually seconds apart and occasionally hours: a reel that
    published while the publisher held no lease, or one whose premiere memory was cleared by an
    operator, is premiered long after it was made, and anchoring on `publishedAt` would compute its turn
    as already over and drop it on the very next rebuild. That was the second half of this bug and it
    hid behind the first.

    While the hold stands this keeps returning the same id, which keeps the reel at slot 0 with an
    unchanged `leadKey` — so the film plays through rather than restarting — and keeps the fingerprint
    identical, so those rebuilds become `checkedAt` touches instead of revision bumps.

    Still re-read from the live query every rebuild, which is what keeps spec 06 §7 honest: a reel that
    loses `visibility == public` mid-play falls out of `reel_query` and off the wall on the next
    rebuild, hold or no hold.
    """
    seen = set(already_premiered)
    moment = now or dt.datetime.now(dt.timezone.utc)

    fresh: tuple[dt.datetime, str] | None = None
    held_duration: float | None = None
    for snap in reel_query(event_id).stream():
        doc = snap.to_dict() or {}
        if snap.id == holding_id:
            try:
                held_duration = float(doc.get("durationSec") or 0.0)
            except (TypeError, ValueError):
                held_duration = 0.0
        if snap.id in seen:
            continue
        stamp = (
            _as_datetime(doc.get("publishedAt"))
            or _as_datetime(doc.get("createdAt"))
            or dt.datetime.min.replace(tzinfo=dt.timezone.utc)
        )
        if fresh is None or stamp > fresh[0]:
            fresh = (stamp, snap.id)

    # A brand-new premiere outranks one already playing: it is the more newsworthy thing, and "newest
    # wins" is the rule this function always had.
    if fresh is not None:
        return fresh[1]

    # `held_duration is None` means the held reel is no longer in the public query — retracted, or
    # superseded. Falling through drops it, which is exactly what spec 06 §7 requires.
    started = _as_datetime(holding_since)
    if holding_id and held_duration is not None and started is not None:
        hold = (held_duration or PREMIERE_FALLBACK_HOLD_SEC) + PREMIERE_GRACE_SEC
        if (moment - started).total_seconds() < hold:
            return holding_id
    return None


# ---------------------------------------------------------------- the write


@dataclass
class Written:
    changed: bool
    revision: int
    fingerprint: str


@firestore.transactional
def _publish(
    transaction: firestore.Transaction,
    ref: firestore.DocumentReference,
    built: program.Program,
    trigger: str,
    holder: str,
    premiered: str | None,
) -> Written:
    snap = ref.get(transaction=transaction)
    current = snap.to_dict() or {}
    revision = int(current.get("revision") or 0)

    history = [r for r in (current.get("premieredReelIds") or []) if isinstance(r, str)]
    # **New** to the history, not merely present in this build. During a premiere's hold (`premiere_reel`)
    # the same reel is passed on every rebuild, and treating that as a reason to write would bump the
    # revision every nudge for the whole length of the film — the exact churn the fingerprint check
    # below exists to avoid. The term is here so the *first* premiere write cannot be skipped by a
    # fingerprint that happened to match; a hold is not a first.
    is_new_premiere = bool(premiered) and premiered not in history

    if snap.exists and current.get("fingerprint") == built.fingerprint and not is_new_premiere:
        # Nothing the viewer could see has changed. Skipping the write is not just thrift: even though
        # B1 made the client's slot-0 reset conditional on `leadKey`, a snapshot still costs the client
        # a fresh render pass, so a revision bump for identical content is still churn worth avoiding
        # — once every fallback interval, forever.
        transaction.set(ref, {"checkedAt": fs.SERVER_TIMESTAMP}, merge=True)
        return Written(False, revision, built.fingerprint)

    if is_new_premiere and premiered is not None:
        history.append(premiered)

    # The hold's anchor (`premiere_reel`). Written only when a premiere *starts*, so a rebuild during
    # the hold cannot keep pushing the clock forward and pin a film on the wall forever. Cleared when
    # nothing is premiering, so a stale anchor can never resurrect a reel whose turn ended.
    premiere_state: dict[str, Any] = {}
    if is_new_premiere:
        premiere_state = {"premiereReelId": premiered, "premiereStartedAt": fs.SERVER_TIMESTAMP}
    elif not premiered:
        premiere_state = {"premiereReelId": None, "premiereStartedAt": None}

    transaction.set(
        ref,
        {
            "revision": revision + 1,
            "activeStageId": built.active_stage_id,
            "theme": built.theme,
            "slots": built.slots,
            "updatedAt": fs.SERVER_TIMESTAMP,
            "checkedAt": fs.SERVER_TIMESTAMP,
            # Operational, not sensitive — this document is world-readable (spec 09 §3), so it
            # carries the show and the reason for it and nothing about a person.
            "fingerprint": built.fingerprint,
            # B1: non-null only when slot 0 is a reel premiere or bounty takeover. The client resets to
            # slot 0 exactly when this changes, instead of on every revision (see `program._lead_key`).
            "leadKey": built.lead_key,
            "trigger": trigger,
            "heroCount": built.hero_count,
            "slotCount": len(built.slots),
            "publishedBy": holder,
            "premieredReelIds": history[-PREMIERE_MEMORY:],
            **premiere_state,
        },
        merge=True,
    )
    return Written(True, revision + 1, built.fingerprint)


def publish(
    event_id: str,
    built: program.Program,
    *,
    trigger: str,
    holder: str,
    premiered: str | None = None,
) -> Written:
    return _publish(
        fs.db().transaction(),
        fs.kiosk_playlist_ref(event_id),
        built,
        trigger,
        holder,
        premiered,
    )


def playlist(event_id: str) -> dict[str, Any]:
    snap = fs.kiosk_playlist_ref(event_id).get()
    return (snap.to_dict() or {}) if snap.exists else {}


def _as_datetime(value: Any) -> dt.datetime | None:
    """Firestore hands back tz-aware datetimes; anything else is a hand-seeded document."""
    if isinstance(value, dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)
    return None
