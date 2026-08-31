"""SELECT — spec 06 §3 step 1: which photographs this reel is allowed to be made of.

Deterministic on purpose, and it is the step that carries the trust architecture into the reel: a
language model never widens this set. The persona filter, the visibility gate, the aesthetic floor
and the VIP floor are all arithmetic here; the model receives the survivors as *text evidence* and
chooses an order and a story. So a reel can never contain a photograph that was not already eligible
for the ring it is published to, whatever the storyboard says.

Two rules in this file are spec text rather than judgement:

- **Visibility ≥ audienceRing.** A Ring-2 (public) reel draws only from `visibility == 'public'`
  media; a Ring-1 (`main_character`) reel additionally draws that person's `pool` items, and only
  that person's. There is no path from a `self` item into any reel.
- **The VIP floor is a floor, not a boost** (spec 11 §3.3): every tier-0/1 person with eligible media
  is *reserved* a slot before diversity sampling spends the budget, because bad luck in a weighted
  draw can exclude a principal from their own wedding film and never should.

The diversity sampler is the same idea as the kiosk's (`publisher/program.py`), deliberately: round
robin across moment tags and face clusters so a reel is not eleven photographs of the same three
people doing the same thing. Unlike the kiosk's, it never repeats — a reel is finite, and running out
of variety means a shorter reel, not a duplicated shot.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from schemas.common import MediaKind, MediaStatus, Visibility
from schemas.reel import ReelPersona
from shared import fs
from shared.settings import (
    DEFAULT_TIER,
    REEL_AESTHETIC_FLOOR,
    REEL_CANDIDATE_CAP,
    REEL_CANDIDATE_FETCH,
    REEL_RECAP_AESTHETIC_FLOOR,
    REEL_RECAP_MIN_CANDIDATES,
    VIP_WEIGHT_BY_TIER,
)

#: Tiers that earn a reserved slot. Spec 11 §3.3's "≥1 slot per tier-0/1 person with eligible media".
VIP_FLOOR_TIERS = (0, 1)


@dataclass
class Candidate:
    """One eligible photograph, flattened to exactly what the reel needs.

    Everything here is *stored* — the Curator's caption and tags, the Face Indexer's boxes, the
    Guardian-gated `visibility`. Nothing is recomputed and no model is consulted, so a reel is a
    function of state that already exists (which is also what makes `--offline` smoke rows possible).
    """

    media_id: str
    display_uri: str
    width: int
    height: int
    aesthetic: float = 0.0
    is_highlight: bool = False
    caption: str = ""
    moment_tags: list[str] = field(default_factory=list)
    cultural_elements: list[str] = field(default_factory=list)
    stage_id: str | None = None
    captured_at: dt.datetime | None = None
    people_count: int | None = None
    #: Normalised face boxes as `[x, y, w, h]`, largest first — the Ken Burns anchor (`edl.py`).
    face_boxes: list[list[float]] = field(default_factory=list)
    person_ids: list[str] = field(default_factory=list)
    cluster_ids: list[str] = field(default_factory=list)
    bounty_id: str | None = None
    top_tier: int = DEFAULT_TIER

    @property
    def vip_weight(self) -> float:
        return VIP_WEIGHT_BY_TIER.get(self.top_tier, 1.0)

    @property
    def primary_moment(self) -> str:
        return self.moment_tags[0] if self.moment_tags else (self.stage_id or "untagged")

    @property
    def primary_cluster(self) -> str:
        if self.person_ids:
            return f"p:{sorted(self.person_ids)[0]}"
        if self.cluster_ids:
            return f"c:{sorted(self.cluster_ids)[0]}"
        return "nobody"


# ---------------------------------------------------------------- reads


def _people(event_id: str) -> tuple[dict[str, int], dict[str, str]]:
    """`(personId → tier, personId → displayName)` in one pass.

    Read fresh per commission rather than cached: `tier` is the input to a *deterministic* ranking and
    selection floor (spec 11 §3.3), so a stale value would silently mean a promoted person stops being
    reserved a slot — the exact failure a host would report as a bug. Dozens of documents at most.
    """
    tiers: dict[str, int] = {}
    names: dict[str, str] = {}
    for snap in fs.people_col(event_id).stream():
        doc = snap.to_dict() or {}
        tiers[snap.id] = int(doc.get("tier", DEFAULT_TIER))
        names[snap.id] = str(doc.get("displayName") or "someone")
    return tiers, names


def _query(event_id: str, visibilities: list[str]) -> firestore.Query:
    """Ordered by aesthetic, not by time.

    The gallery orders by capture time because a guest is browsing; a reel is *choosing*, and the
    ordering that matters is "which of these is worth a shot". Needs its own composite index
    (`visibility, status, curator.aestheticScore desc`) — deliberately not the existing
    `visibility+status+isHighlight+aestheticScore` one, because filtering on `isHighlight` here would
    make a reel impossible at an event where the Curator has not called anything a highlight yet, and
    the aesthetic floor plus the persona lens are the right bar (`choose` below applies both).
    """
    return (
        fs.media_col(event_id)
        .where(filter=FieldFilter("visibility", "in", visibilities))
        .where(filter=FieldFilter("status", "==", MediaStatus.INDEXED.value))
        .order_by("curator.aestheticScore", direction=firestore.Query.DESCENDING)
        .limit(REEL_CANDIDATE_FETCH)
    )


def _to_candidate(media_id: str, doc: dict[str, Any], tiers: dict[str, int]) -> Candidate | None:
    if doc.get("deleted") or doc.get("duplicateOf"):
        return None
    # Photos only. Spec 06 §3 step 5 allows video subclips from originals; this build renders from
    # the `display_1600` derived variant so `sa-render` needs no raw-bucket grant, and a video has no
    # derived still worth two seconds of a reel. A video candidate is dropped here rather than
    # half-handled in the filtergraph.
    if doc.get("kind") != MediaKind.PHOTO.value:
        return None
    uri = doc.get("displayUri") or doc.get("thumbUri")
    if not uri:
        return None
    curator = doc.get("curator") or {}
    faces = doc.get("faces") or []
    person_ids = [str(p) for p in (doc.get("albumOf") or []) if p]
    boxes: list[list[float]] = []
    for face in faces:
        box = (face or {}).get("box") or {}
        try:
            boxes.append(
                [float(box["x"]), float(box["y"]), float(box["w"]), float(box["h"])]
            )
        except (KeyError, TypeError, ValueError):
            continue
    boxes.sort(key=lambda b: b[2] * b[3], reverse=True)
    tier = min((tiers.get(p, DEFAULT_TIER) for p in person_ids), default=DEFAULT_TIER)
    return Candidate(
        media_id=media_id,
        display_uri=str(uri),
        width=int(doc.get("width") or 0),
        height=int(doc.get("height") or 0),
        aesthetic=float(curator.get("aestheticScore") or 0.0),
        is_highlight=bool(curator.get("isHighlight")),
        caption=str(curator.get("caption") or ""),
        moment_tags=[str(t) for t in (curator.get("momentTags") or [])],
        cultural_elements=[str(t) for t in (curator.get("culturalElements") or [])],
        stage_id=(str(curator.get("stageId")) if curator.get("stageId") else None),
        captured_at=doc.get("capturedAt"),
        people_count=curator.get("peopleCountEstimate"),
        face_boxes=boxes,
        person_ids=person_ids,
        cluster_ids=[str(f.get("clusterId")) for f in faces if (f or {}).get("clusterId")],
        bounty_id=(str(doc.get("bountyId")) if doc.get("bountyId") else None),
        top_tier=tier,
    )


def fetch(
    event_id: str,
    *,
    persona: ReelPersona,
    audience_ring: int,
    person_id: str | None = None,
    stage_id: str | None = None,
) -> tuple[list[Candidate], dict[str, str]]:
    """Read the eligible pool and cut it to `REEL_CANDIDATE_CAP`. Returns (candidates, personNames).

    The names are read here rather than in `agent.py` because they are only ever used to make the
    *brief* legible ("his mother wiping his face"). They are never an identity claim: `person_ids`
    carries the deterministic ArcFace match, and the display name is decoration on top of it.
    """
    tiers, names = _people(event_id)
    visibilities = [Visibility.PUBLIC.value]
    if audience_ring <= 1:
        # A private reel about one person may also use that person's Ring-1 items — and only that
        # person's, which the `albumOf` filter below enforces after the read (spec 06 §7).
        visibilities.append(Visibility.POOL.value)

    pool: list[Candidate] = []
    for snap in _query(event_id, visibilities).stream():
        candidate = _to_candidate(snap.id, snap.to_dict() or {}, tiers)
        if candidate is not None:
            pool.append(candidate)

    chosen = choose(
        pool,
        persona=persona,
        person_id=person_id,
        stage_id=stage_id,
        audience_ring=audience_ring,
        on_fallback=lambda message: fs.ops_alert(event_id, "reel_persona_fallback", message),
    )
    return chosen, names


# ---------------------------------------------------------------- the pure decision


def _persona_filter(
    candidates: Iterable[Candidate],
    *,
    persona: ReelPersona,
    person_id: str | None,
    stage_id: str | None,
    audience_ring: int,
    floor: float = REEL_AESTHETIC_FLOOR,
) -> list[Candidate]:
    """Spec 06 §2.2's lens applied to *eligibility*; the lens applied to *pacing* is the prompt's.

    `floor` is a parameter rather than the constant so `choose` can try the recap's higher bar first
    (see `REEL_RECAP_AESTHETIC_FLOOR`). `is_highlight` still bypasses it at any setting: the Curator
    only sets that flag at `aestheticScore >= 0.75` *and* "shows a moment", so a highlight is already
    above every floor this function is ever handed — the bypass exists for the honest edge case where
    a stored score is missing but the judgement was recorded.
    """
    out: list[Candidate] = []
    for c in candidates:
        if c.aesthetic < floor and not c.is_highlight:
            continue
        if audience_ring <= 1 and person_id:
            # Ring-1 items are only permitted when they belong to the subject of the reel.
            if person_id not in c.person_ids:
                continue
        if persona is ReelPersona.MAIN_CHARACTER:
            if not person_id or person_id not in c.person_ids:
                continue
        elif persona is ReelPersona.STAGE_RECAP:
            if stage_id and c.stage_id != stage_id:
                continue
        elif persona is ReelPersona.EVENT_RECAP:
            # The whole arc is eligible (spec 13 §8) — the floor above is the only cut here; the
            # cross-stage spread happens in `choose`'s bucketing, not by exclusion.
            pass
        elif persona is ReelPersona.COUPLE:
            # The couple's film is about the principals, plus the moments they are the subject of.
            # `top_tier <= 1` is the deterministic test — spec 11 §4: VIP is policy, not memory.
            if c.top_tier > 1:
                continue
        elif persona is ReelPersona.GUEST_ENERGY:
            # The inverse lens: the crowd. A frame with nobody identified still counts (a dance floor
            # from the back is exactly this reel's material); a principal's portrait does not.
            if c.top_tier == 0 and (c.people_count or 0) <= 2:
                continue
        out.append(c)
    return out


#: B5's fallback ceiling. A reel is a permanent, shareable file — unlike 6 seconds on the kiosk wall
#: — so when the persona lens empties, the degrade stops at "someone the event has identified as
#: elevated" rather than reaching all the way to an unrelated guest's random landscape (`top_tier`
#: defaults to `DEFAULT_TIER`, i.e. an unidentified or never-promoted subject, which is exactly what
#: this ceiling excludes). No spec pins this number — flagged for HANDOFF §9.
FALLBACK_MAX_TIER = 2


def choose(
    candidates: list[Candidate],
    *,
    persona: ReelPersona,
    person_id: str | None = None,
    stage_id: str | None = None,
    audience_ring: int = 2,
    cap: int = REEL_CANDIDATE_CAP,
    on_fallback: Callable[[str], None] | None = None,
) -> list[Candidate]:
    """Pure: persona filter → VIP floor → diversity round-robin → cap.

    Falls back rather than fails. A `couple` reel at an event where nobody has been promoted would
    filter to nothing, and returning an empty list there would mean the demo's headline artifact
    silently never appears; so an empty persona filter degrades — not to the fully unfiltered pool,
    but to `FALLBACK_MAX_TIER` and below (B5). The reel is then simply *about the event* rather than
    about the couple, and the brief the model writes says so because the evidence says so, but a
    guest's unrelated landscape still cannot end up in a couple's permanent wedding film. `on_fallback`,
    when given, is called with a diagnostic message exactly when this path fires — this function stays
    pure (no Firestore, no event_id), so raising the actual `ops/` alert is the caller's job
    (`fetch()` below).
    """
    filtered = _persona_filter(
        candidates,
        persona=persona,
        person_id=person_id,
        stage_id=stage_id,
        audience_ring=audience_ring,
    )

    # The recap's raised floor (spec 13 §8's film is the one permanent artifact — see
    # `REEL_RECAP_AESTHETIC_FLOOR`). Tried *after* the ordinary filter rather than instead of it, so
    # the comparison is against a known-good baseline: if the stricter pass leaves enough material for
    # a full-length film, use it; otherwise keep what the ordinary floor found. A thin event therefore
    # gets a softer recap and never an absent one, and the decision is one `len()` rather than a
    # separate code path that could diverge from the pass above it.
    if persona is ReelPersona.EVENT_RECAP and len(filtered) >= REEL_RECAP_MIN_CANDIDATES:
        stricter = _persona_filter(
            candidates,
            persona=persona,
            person_id=person_id,
            stage_id=stage_id,
            audience_ring=audience_ring,
            floor=REEL_RECAP_AESTHETIC_FLOOR,
        )
        if len(stricter) >= REEL_RECAP_MIN_CANDIDATES:
            filtered = stricter

    if not filtered:
        filtered = [
            c
            for c in candidates
            if (c.aesthetic >= REEL_AESTHETIC_FLOOR or c.is_highlight) and c.top_tier <= FALLBACK_MAX_TIER
        ]
        if on_fallback:
            on_fallback(
                f"{persona.value} reel: the persona filter matched nothing, falling back to "
                f"tier<={FALLBACK_MAX_TIER} candidates ({len(filtered)} of {len(candidates)})"
            )
    if not filtered:
        return []

    by_score = sorted(filtered, key=lambda c: (-c.aesthetic, c.media_id))

    # --- the VIP floor, taken first so sampling cannot spend the budget past it.
    reserved: list[Candidate] = []
    claimed: set[str] = set()
    for tier in VIP_FLOOR_TIERS:
        people = sorted({p for c in by_score for p in c.person_ids if c.top_tier == tier})
        for pid in people:
            best = next(
                (c for c in by_score if pid in c.person_ids and c.media_id not in claimed), None
            )
            if best is not None and len(reserved) < cap:
                reserved.append(best)
                claimed.add(best.media_id)

    # --- diversity round-robin over the rest. For `event_recap` the bucket key leads with the
    # *stage* (spec 13 §8): the round-robin then spreads the cap across the event's whole arc —
    # Day 1's arrival gets a shot into the film even when Day 4's viewpoint out-scores it, which
    # is the difference between a recap of the trip and a montage of its single best hour.
    buckets: dict[str, list[Candidate]] = {}
    for c in by_score:
        if c.media_id in claimed:
            continue
        if persona is ReelPersona.EVENT_RECAP:
            key = f"{c.stage_id}|{c.primary_cluster}"
        else:
            key = f"{c.primary_moment}|{c.primary_cluster}"
        buckets.setdefault(key, []).append(c)

    order = sorted(buckets, key=lambda k: (-buckets[k][0].aesthetic, k))
    chosen = list(reserved)
    while len(chosen) < cap and any(buckets[k] for k in order):
        for key in order:
            if not buckets[key]:
                continue
            chosen.append(buckets[key].pop(0))
            if len(chosen) >= cap:
                break

    # Presented to the model best-first: the storyboard's own ordering is a *narrative* decision, and
    # a candidate list already in chronological order would invite it to just copy that.
    return sorted(chosen, key=lambda c: (-c.aesthetic, c.media_id))
