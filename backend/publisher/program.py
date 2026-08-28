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
    VIP_WEIGHT_BY_TIER,
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


# ---------------------------------------------------------------- the score


def recency_decay(captured_at: dt.datetime | None, now: dt.datetime) -> float:
    """`0.5 ** (age / 20 min)` (spec 04 §4). A missing timestamp is treated as now: intake always
    writes one (EXIF or arrival), so the only way to get here is a hand-seeded document."""
    if captured_at is None:
        return 1.0
    age_min = max(0.0, (now - captured_at).total_seconds() / 60.0)
    return 0.5 ** (age_min / KIOSK_RECENCY_HALF_LIFE_MIN)


def stage_match(stage_id: str | None, active: str | None, previous: str | None) -> float:
    """Active ×1.0, previous ×0.4 (spec 04 §4). Everything else — including a photo the Curator
    could not place — takes `KIOSK_STAGE_MATCH_OTHER`, which no spec pins."""
    if active and stage_id == active:
        return KIOSK_STAGE_MATCH_ACTIVE
    if previous and stage_id == previous:
        return KIOSK_STAGE_MATCH_PREVIOUS
    return KIOSK_STAGE_MATCH_OTHER


def _base_factors(
    c: Candidate, now: dt.datetime, active: str | None, previous: str | None
) -> dict[str, float]:
    return {
        "aesthetic": float(c.aesthetic or 0.0),
        "recency": recency_decay(c.captured_at, now),
        "stageMatch": stage_match(c.stage_id, active, previous),
        "vipWeight": float(c.vip_weight or 1.0),
    }


def _base_score(factors: dict[str, float]) -> float:
    return factors["aesthetic"] * factors["recency"] * factors["stageMatch"] * factors["vipWeight"]


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
    pool = [(c, _base_factors(c, now, active, previous)) for c in candidates]
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
) -> Program:
    """Assemble the slot list. Ordering rules, in the order they win:

    1. **A reel premiere takes over the screen** (spec 04 §4). It leads the program, and because the
       client resets to slot 0 whenever the playlist changes, "leads" and "interrupts" are the same
       thing — there is no need to inject anything mid-render, which spec 06 §7 forbids anyway.
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
        candidates, now=now, active=active_stage_id, previous=previous_stage_id
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
    )


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
