"""Building one kiosk program — spec 04 §4's hero score and slot list, as a pure function.

The kiosk is **not** the public gallery on a TV. It is a directed show: a playlist that deterministic
code maintains, that agents advise but never write, and that a dumb fullscreen client renders. This
module is the deciding half, and it is deliberately pure — no Firestore, no clock of its own, no I/O.
Three things follow from that, all of them load-bearing:

- The ranking is auditable. Spec 04 §4's "Why this photo?" overlay shows the factor breakdown the
  publisher computed, and because the numbers are stored on the slot rather than recomputed by the
  viewer, the card cannot disagree with the decision. That is the same truthful-by-construction
  discipline the trust rail uses elsewhere, applied to ranking.
- No language model is anywhere near it. `vipWeight` comes from the host-declared `tier` on a person
  document (spec 11 §3.3: VIP is policy, not memory), `stageMatch` from the event's own timeline,
  and everything else from the Curator's already-stored score. The wall follows the event because
  the arithmetic says so.
- It is checkable without infrastructure. `scripts/smoke_autonomy.py --program-only` runs the
  diversity acceptance criterion (spec 04 §6: no face cluster twice in any five consecutive hero
  slots) against fixtures with no network and no spend — the same shape as the Guardian's gate table.

Two constants here are *not* pinned by any spec and are recorded in HANDOFF §9 rather than chosen
silently: `KIOSK_STAGE_MATCH_OTHER` (the spec pins only active ×1.0 and previous ×0.4) and
`KIOSK_DIVERSITY_PENALTY` (named as a factor, never given a value).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from schemas.common import UNINFORMATIVE_SETTINGS
from shared.settings import (
    KIOSK_DIVERSITY_PENALTY,
    KIOSK_DIVERSITY_WINDOW,
    KIOSK_HERO_HOLD_SEC,
    KIOSK_HERO_SHARE,
    KIOSK_JUST_IN_WINDOW_SEC,
    KIOSK_LEADERBOARD_EVERY_SEC,
    KIOSK_PROGRAM_SECONDS,
    KIOSK_RECENCY_HALF_LIFE_MIN,
    KIOSK_STAGE_MATCH_ACTIVE,
    KIOSK_STAGE_MATCH_OTHER,
    KIOSK_STAGE_MATCH_PREVIOUS,
    KIOSK_TAKEOVER_FRESH_MINUTES,
    VIP_WEIGHT_BY_TIER,
    WORLD_MIN_CORPUS,
    WORLD_ONTOPIC_COMMON_SHARE,
    WORLD_ONTOPIC_RARE_SHARE,
    WORLD_ONTOPIC_WEIGHTS,
)

#: How long the non-hero slots occupy, mirroring `frontend/src/lib/kiosk.ts::slotHoldSec`. The client
#: owns the real timing; these are only used here to space the interleaves across the program.
LEADERBOARD_HOLD_SEC = 8
JUST_IN_HOLD_SEC = 8
LEADERBOARD_TOP_N = 5

#: How many hero slots a full program carries: ~60% of a ~5-minute show at 6 s each (spec 04 §4).
HERO_SLOTS = max(1, int(KIOSK_PROGRAM_SECONDS * KIOSK_HERO_SHARE / KIOSK_HERO_HOLD_SEC))


def vip_weight(tiers: list[int]) -> float:
    """Spec 04 §4: the **max** across faces in frame, so a guest photographed with a Principal
    inherits the ×3.0. That is not a rounding of the rule, it is the point of it — a guest's best
    route to the big screen is being in frame with the couple, which is the social dynamic the wall
    should reward. Pure guest shots still rotate in via `diversityPenalty` and `just_in`.
    """
    weights = [VIP_WEIGHT_BY_TIER.get(int(t), 1.0) for t in tiers if t is not None]
    return max(weights) if weights else 1.0


@dataclass(frozen=True)
class Candidate:
    """One public, indexed media item as the ranker sees it. Built by `runner.py` from a doc."""

    media_id: str
    aesthetic: float
    captured_at: dt.datetime | None
    uploaded_at: dt.datetime | None
    stage_id: str | None
    moment_tags: tuple[str, ...] = ()
    #: Face cluster (or claimed person) ids, plus moment tags — the two things spec 04 §4 says must
    #: not repeat inside the diversity window. Kept as one key set because the rule is one rule.
    dedupe_keys: frozenset[str] = frozenset()
    vip_weight: float = 1.0
    #: The Curator's `sceneSetting` (spec 03 §5.1), or `None` for a hand-seeded fixture that never
    #: went through the Curator. Feeds `onTopic` below and nothing else — this field is never
    #: compared for equality against anything that decides exposure.
    scene_setting: str | None = None


@dataclass(frozen=True)
class SceneContext:
    """The world model's hard layer (spec 03 §5.1), shaped for the ranking rather than for the
    Story Director's prompt — that shaping lives in `directors/story/world.py::WorldSnapshot`, a
    different dataclass built from the same `shared/coverage.py` counts, because `program.py` is the
    kiosk's pure ranking core and must not import a director-agent module. `store.py` builds this one
    directly from the coverage shards it already has in hand.

    `enabled` is the whole feature's kill switch, and it is off by construction whenever a caller does
    not pass one — every existing call site in `scripts/smoke_autonomy.py` and every prior behaviour
    is therefore unchanged unless `store.py` explicitly opts an event in.
    """

    #: `sceneSetting → count`, summed across the event's stages (`coverage.scene_totals`).
    totals: dict[str, int] = field(default_factory=dict)
    #: Total photos with an *informative* setting — the denominator `_on_topic` shares against.
    #: Excludes `closeup_detail`/`unknown`, so a stage of nothing but ring shots cannot dilute every
    #: real setting into looking artificially common.
    informative_total: int = 0
    #: `stageId → the host-declared expected setting` (`EventStage.expectedSetting`). The cold-start
    #: prior: a photo matching its own stage's declared setting is never demoted, even at zero corpus.
    expected_by_stage: dict[str, str] = field(default_factory=dict)
    #: Gated on `access.mode == 'open' and access.kioskPublic` (`store.py::scene_context`). On an
    #: invite-only or kiosk-private event, Ring 2 already resolves to "the people in this event," not
    #: the internet — an off-topic photo there is a non-problem, and it is also exactly the small,
    #: low-corpus event where this mechanism has no reliable signal anyway.
    enabled: bool = False


@dataclass
class Program:
    """The result: what to write, plus why, plus enough to decide whether to write at all."""

    slots: list[dict[str, Any]] = field(default_factory=list)
    active_stage_id: str | None = None
    theme: str | None = None
    hero_count: int = 0
    #: Hash of the *decisions* (slot types and ids), deliberately excluding the factor floats.
    #: Recency decay changes those on every rebuild while the program itself is identical, and a
    #: rewrite the kiosk client can see costs a visible restart of the show (it resets to slot 0 on
    #: every snapshot). So the fingerprint is what decides whether a rebuild is worth a revision.
    fingerprint: str = ""
    #: The identity of slot 0 when — and only when — it is a reel premiere or a bounty takeover;
    #: `None` otherwise (B1). A hero-only rebuild changes `fingerprint` on almost every tick (a new
    #: photo enters the pool, recency reorders the top of the list), and the client used to treat every
    #: one of those as "the lead changed" and restart the show from slot 0. That is only correct for an
    #: actual interrupt — the client resets **only** when `leadKey` itself changes, so a tail-only
    #: rebuild leaves the viewer's position alone.
    lead_key: str | None = None


# ---------------------------------------------------------------- the score


def recency_decay(
    captured_at: dt.datetime | None, uploaded_at: dt.datetime | None, now: dt.datetime
) -> float:
    """`0.5 ** (age / 20 min)` (spec 04 §4), taken over the **younger** of `capturedAt` and
    `uploadedAt` (B2). A forwarded photo can be public and brand new while its EXIF capture time is
    hours or days old — `capturedAt` alone decays it to near zero before it ever reaches a hero slot,
    which defeats the exact "your photo is on the wall" guarantee `store.RECENT_LIMIT`'s query exists
    to serve. Missing timestamps are dropped from the comparison rather than forced to either extreme;
    if both are missing the age is treated as zero, matching the old "missing means now" behaviour —
    intake always writes both (EXIF/arrival and server time), so the only way to get here is a
    hand-seeded document."""
    ages = [
        max(0.0, (now - t).total_seconds() / 60.0) for t in (captured_at, uploaded_at) if t is not None
    ]
    age_min = min(ages) if ages else 0.0
    return 0.5 ** (age_min / KIOSK_RECENCY_HALF_LIFE_MIN)


def stage_match(stage_id: str | None, active: str | None, previous: str | None) -> float:
    """Active ×1.0, previous ×0.4 (spec 04 §4). Everything else — including a photo the Curator
    could not place — takes `KIOSK_STAGE_MATCH_OTHER`, which no spec pins."""
    if active and stage_id == active:
        return KIOSK_STAGE_MATCH_ACTIVE
    if previous and stage_id == previous:
        return KIOSK_STAGE_MATCH_PREVIOUS
    return KIOSK_STAGE_MATCH_OTHER


def on_topic(candidate: Candidate, scenes: SceneContext) -> float:
    """A **demotion**, never a gate — this is the whole reason it lives here and not in
    `shared/visibility.py::decide`. At a plausible 95% precision on a 2,000-photo event where 1% is
    genuinely off-topic, the arithmetic is ~19 true positives against ~99 false positives: as a ranking
    factor a false positive costs one hero slot and nothing else — the photo keeps its gallery entry,
    its albums, its reel eligibility and its owner. As an exposure gate the same error would suppress
    99 legitimate photos with no way to release them.

    Every early-return below is "no opinion", not "on topic" — the two read the same as a 1.0
    multiplier, but the reasons are different and worth keeping distinct in the comments even though
    the code cannot tell them apart:

    - the mechanism is off for this event (`enabled=False` — an invite-only or kiosk-private event, or
      one `store.py` has not opted in);
    - the corpus is too small for a share to mean anything (`WORLD_MIN_CORPUS`, mirroring
      `STAGE_PRIOR_FLAT`'s "a flat prior contributes no ordering information" in
      `workers/curate/fusion.py`);
    - the photo itself carries no location information (`closeup_detail`/`unknown` — punishing the
      absence of evidence would be the same mistake fusion.py avoids for missing EXIF);
    - the photo matches its own stage's host-declared `expectedSetting` — the cold-start prior that
      keeps a hill-station wedding's baraat from reading as an outlier the moment it starts, before the
      observed distribution has any evidence at all.

    Only once none of those apply does the observed share actually demote anything.
    """
    if not scenes.enabled:
        return 1.0
    if scenes.informative_total < WORLD_MIN_CORPUS:
        return 1.0
    setting = candidate.scene_setting
    if not setting or setting in UNINFORMATIVE_SETTINGS:
        return 1.0
    if candidate.stage_id and scenes.expected_by_stage.get(candidate.stage_id) == setting:
        return 1.0

    share = scenes.totals.get(setting, 0) / scenes.informative_total
    full, mid, low = WORLD_ONTOPIC_WEIGHTS
    if share >= WORLD_ONTOPIC_COMMON_SHARE:
        return full
    if share >= WORLD_ONTOPIC_RARE_SHARE:
        return mid
    return low


def _base_factors(
    c: Candidate,
    now: dt.datetime,
    active: str | None,
    previous: str | None,
    scenes: SceneContext,
) -> dict[str, float]:
    return {
        "aesthetic": float(c.aesthetic or 0.0),
        "recency": recency_decay(c.captured_at, c.uploaded_at, now),
        "stageMatch": stage_match(c.stage_id, active, previous),
        "vipWeight": float(c.vip_weight or 1.0),
        "onTopic": on_topic(c, scenes),
    }


def _base_score(factors: dict[str, float]) -> float:
    return (
        factors["aesthetic"]
        * factors["recency"]
        * factors["stageMatch"]
        * factors["vipWeight"]
        * factors["onTopic"]
    )


def _collides(c: Candidate, others: list[Candidate]) -> bool:
    if not c.dedupe_keys:
        return False
    return any(c.dedupe_keys & o.dedupe_keys for o in others)


def _sort_key(item: tuple[Candidate, float]) -> tuple[float, float]:
    """Score desc, then upload recency desc. The tie-break matters: a demo event runs at
    `publicFloor 0.0`, so several candidates can legitimately score 0.0 on aesthetic, and without a
    second term their order would be whatever Firestore happened to return."""
    candidate, score = item
    stamp = candidate.uploaded_at.timestamp() if candidate.uploaded_at else 0.0
    return (score, stamp)


def select_heroes(
    candidates: list[Candidate],
    *,
    now: dt.datetime,
    active: str | None,
    previous: str | None,
    limit: int = HERO_SLOTS,
    scenes: SceneContext = SceneContext(),
) -> list[tuple[Candidate, dict[str, float]]]:
    """Greedy selection under the diversity rule (spec 04 §4/§6).

    The rule is enforced as a **deferral, not an exclusion**, and the distinction is the whole design:
    a candidate that repeats a face cluster or moment tag inside the window is only chosen when
    nothing else is left. So spec 04 §6's criterion holds exactly whenever the event has enough
    distinct subjects to satisfy it — and at a five-guest party, where five consecutive distinct
    clusters are arithmetically impossible, the wall still shows photos instead of going dark. The
    `diversityPenalty` multiplier stays in the stored factors as the honest record of a slot that was
    filled by a repeat, which is exactly what the "Why this photo?" card should say about it.

    A final pass fixes collisions across the loop boundary — the client cycles the program, so slot
    N-1 and slot 0 are consecutive on screen even though they are not consecutive in this list.
    """
    pool = [(c, _base_factors(c, now, active, previous, scenes)) for c in candidates]
    remaining = [(c, f, _base_score(f)) for c, f in pool]
    chosen: list[tuple[Candidate, dict[str, float]]] = []

    while remaining and len(chosen) < limit:
        window = [c for c, _ in chosen[-(KIOSK_DIVERSITY_WINDOW - 1) :]]
        clean = [i for i, (c, _f, _b) in enumerate(remaining) if not _collides(c, window)]
        eligible = clean or list(range(len(remaining)))
        penalty = 1.0 if clean else KIOSK_DIVERSITY_PENALTY
        best_index = max(
            eligible, key=lambda i: _sort_key((remaining[i][0], remaining[i][2] * penalty))
        )
        candidate, factors, _base = remaining.pop(best_index)
        chosen.append((candidate, {**factors, "diversity": penalty}))

    _deconflict_loop(chosen)
    for rank, (_candidate, factors) in enumerate(chosen):
        factors["rank"] = rank
    return chosen


def _deconflict_loop(chosen: list[tuple[Candidate, dict[str, float]]]) -> None:
    """One bounded repair pass over the *circular* windows, in place.

    Linear selection cannot see the wrap: the highest-scoring photo leads the program, and if the
    tail happens to recycle the same face cluster the two sit next to each other on screen. One swap
    pass fixes that whenever a non-colliding partner exists, and gives up quietly when the event
    simply does not have enough distinct subjects to satisfy a 5-slot window.
    """
    n = len(chosen)
    if n <= KIOSK_DIVERSITY_WINDOW:
        return
    for i in range(n):
        if not _collides(chosen[i][0], _loop_window(chosen, i)):
            continue
        for j in range(n):
            if j == i:
                continue
            chosen[i], chosen[j] = chosen[j], chosen[i]
            if not _collides(chosen[i][0], _loop_window(chosen, i)) and not _collides(
                chosen[j][0], _loop_window(chosen, j)
            ):
                break
            chosen[i], chosen[j] = chosen[j], chosen[i]  # undo, keep looking


def _loop_window(chosen: list[tuple[Candidate, dict[str, float]]], i: int) -> list[Candidate]:
    """The `KIOSK_DIVERSITY_WINDOW - 1` slots that precede `i` on screen, wrapping around."""
    n = len(chosen)
    return [chosen[(i - k) % n][0] for k in range(1, KIOSK_DIVERSITY_WINDOW) if k < n]


# ---------------------------------------------------------------- the program


def build(
    candidates: list[Candidate],
    *,
    now: dt.datetime,
    active_stage_id: str | None = None,
    previous_stage_id: str | None = None,
    theme: str | None = None,
    premiere_reel_id: str | None = None,
    takeover_bounty_id: str | None = None,
    scenes: SceneContext = SceneContext(),
) -> Program:
    """Assemble the slot list. Ordering rules, in the order they win:

    1. **A reel premiere takes over the screen** (spec 04 §4). It leads the program, and the client
       resets to slot 0 exactly when `Program.lead_key` changes (B1) — so "leads" and "interrupts" are
       the same thing for a premiere or a takeover, with no need to inject anything mid-render (which
       spec 06 §7 forbids anyway), while an ordinary hero-only rebuild leaves the viewer's place alone.
    2. **An escalated bounty is a full-screen mission.** The Story Director escalates precisely
       because the ordinary banner did not work, so it goes in front of the show, not into it.
    3. **`just_in` leads whenever something just went public.** Spec 04 §4 calls that strip the
       "your photo is on the wall" guarantee; putting it first for the two minutes after an upload is
       how the guarantee is actually delivered, rather than hoping the new photo out-ranks a
       Principal's portrait on aesthetic. It is recency-only by design — no score term, no curation.
    4. Then heroes, with a leaderboard and a `just_in` refresh interleaved on spec 04 §4's cadence.

    An event with nothing public yet gets an **empty** slot list rather than a placeholder program.
    The client renders its own pre-show state for that, and inventing slots that resolve to nothing
    would put a shimmering skeleton on a five-metre screen and call it a show.
    """
    slots: list[dict[str, Any]] = []
    if premiere_reel_id:
        slots.append({"type": "reel", "reelId": premiere_reel_id, "premiere": True})
    if takeover_bounty_id:
        slots.append({"type": "bounty_call", "bountyId": takeover_bounty_id})

    heroes = select_heroes(
        candidates, now=now, active=active_stage_id, previous=previous_stage_id, scenes=scenes
    )

    fresh = any(
        c.uploaded_at is not None
        and (now - c.uploaded_at).total_seconds() <= KIOSK_JUST_IN_WINDOW_SEC
        for c in candidates
    )
    if fresh and heroes:
        slots.append(_just_in())

    elapsed = 0.0
    next_leaderboard = float(KIOSK_LEADERBOARD_EVERY_SEC)
    next_just_in = float(KIOSK_LEADERBOARD_EVERY_SEC)
    for candidate, factors in heroes:
        if elapsed >= next_leaderboard:
            slots.append({"type": "leaderboard", "topN": LEADERBOARD_TOP_N})
            elapsed += LEADERBOARD_HOLD_SEC
            next_leaderboard += KIOSK_LEADERBOARD_EVERY_SEC
        if elapsed >= next_just_in:
            slots.append(_just_in())
            elapsed += JUST_IN_HOLD_SEC
            next_just_in += KIOSK_LEADERBOARD_EVERY_SEC
        slots.append(
            {
                "type": "hero",
                "mediaId": candidate.media_id,
                "holdSec": KIOSK_HERO_HOLD_SEC,
                # Stored, never recomputed by a viewer — spec 04 §4's glass-box ranking card.
                "factors": {k: round(float(v), 4) for k, v in sorted(factors.items())},
            }
        )
        elapsed += KIOSK_HERO_HOLD_SEC

    return Program(
        slots=slots,
        active_stage_id=active_stage_id,
        theme=theme,
        hero_count=len(heroes),
        fingerprint=fingerprint(slots, active_stage_id, theme),
        lead_key=_lead_key(slots),
    )


def _lead_key(slots: list[dict[str, Any]]) -> str | None:
    """The identity of slot 0, but **only** when it is a takeover (B1) — a reel premiere or an
    escalated bounty. Every other slot 0 (a `just_in` strip, an ordinary hero) is `None`, because those
    are not interrupts and must not cost the viewer their place in the show. The client resets to slot
    0 exactly when this string changes to a new, non-`None` value."""
    if not slots:
        return None
    lead = slots[0]
    if lead.get("type") == "reel":
        return f"reel:{lead.get('reelId')}"
    if lead.get("type") == "bounty_call":
        return f"bounty:{lead.get('bountyId')}"
    return None


def pick_takeover(bounties: list[dict[str, Any]], now: dt.datetime) -> str | None:
    """Which bounty, if any, has earned the whole screen right now. Pure — `store.takeover_bounty`
    fetches the documents and delegates here, so the rule is checkable with no Firestore.

    Two conditions, and the second one is new (S14). A bounty must have *asked* for the screen —
    `status == 'escalated'`, or an explicit `kioskTakeover`, never an ordinary `active` one, which is
    already a banner in every guest's pocket. And its escalation must still be **fresh**: spec 05 §3
    escalates at half-life and spec 04 §4 hands over the lead slot, but nothing said when that claim
    lapses, so on an event where nobody submits the escalate → expire → reissue cycle owns the wall
    forever. See `KIOSK_TAKEOVER_FRESH_MINUTES` for the measurement that prompted this.

    A bounty with no timestamp at all is treated as stale rather than fresh: the only way to get here
    is a hand-seeded document, and the failure that matters is a poster stuck on a five-metre screen,
    not a poster that never appears.
    """
    best: tuple[dt.datetime, str] | None = None
    for doc in bounties:
        if doc.get("status") != "escalated" and not doc.get("kioskTakeover"):
            continue
        at = doc.get("escalatedAt") or doc.get("createdAt")
        if not isinstance(at, dt.datetime):
            continue
        if (now - at).total_seconds() > KIOSK_TAKEOVER_FRESH_MINUTES * 60:
            continue
        bounty_id = doc.get("bountyId")
        if not bounty_id:
            continue
        if best is None or at > best[0]:
            best = (at, str(bounty_id))
    return best[1] if best else None


def _just_in() -> dict[str, Any]:
    """The client derives this strip's contents itself from the same `uploadedAt` index the publisher
    would use (spec 04 §4: recency only), so the slot carries a window and nothing else."""
    return {"type": "just_in", "liveWindowSec": KIOSK_JUST_IN_WINDOW_SEC}


def fingerprint(
    slots: list[dict[str, Any]], active_stage_id: str | None, theme: str | None
) -> str:
    """A stable hash of the program's *decisions*, ignoring the factor floats (see `Program`)."""
    skeleton = [
        {k: v for k, v in sorted(slot.items()) if k != "factors"} for slot in slots
    ]
    blob = json.dumps(
        {"slots": skeleton, "stage": active_stage_id, "theme": theme},
        sort_keys=True,
        default=str,
    )
    return hashlib.sha1(blob.encode()).hexdigest()[:16]
