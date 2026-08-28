"""Bounty submission validation and the points award — spec 05 §3's second half.

A guest taps a banner, photographs the thing, and the upload flows the *normal* pipeline with
`bountyId` stamped at intent time (spec 01 §3), routed to `priority-queue` so validation is not stuck
behind a burst (spec 09 §2). This module is what happens after the pipeline finishes with it.

Three decisions, each of which was the alternative to something worse:

**It runs in the tick, not in a worker.** Spec 05 §3 puts the check "after curate completes", and the
first design did exactly that. It is wrong for two reasons that only show up under load. The identity
half of the criteria comes from the Face Indexer, which runs *in parallel* with the Curator — so a
check fired on curate completion regularly cannot see who is in the frame, and would have to either
guess or re-queue itself. And the award is money-path-adjacent: two workers validating two submissions
to the same bounty concurrently is exactly the double-award spec 05 §5 forbids. Running here means the
tick lease already serialises every award for an event, so the race cannot exist rather than being
defended against. The cost is latency, and the demo cadence is 30 s (spec 09 §5) — the guest sees
their photo appear immediately on the wall either way; only the points wait for a tick.

**The model judges the moment and nothing else.** `targetVip` is settled by comparing the bounty's
personId against the Face Indexer's `albumOf` — a deterministic set membership on a 512-d ArcFace
match, not an opinion. Quality is the Curator's already-stored `aestheticScore`. What is left is the
one genuinely contextual question ("does a photo captioned *three relatives pressing turmeric onto a
laughing bride* satisfy 'get the haldi paste going on'?"), and that is what flash-lite is asked.

**The prompt is text, not the photograph.** The Curator already looked at the image and produced a
caption, moment tags, a stage and a score; re-sending the bytes would cost ~260 image tokens and a GCS
read to re-derive a worse version of what is already on the document. It also keeps `sa-api` out of
the media buckets entirely — the service that runs the director cannot read a guest's photograph.
"""

from __future__ import annotations

import datetime as dt
import functools
from dataclasses import dataclass, field
from typing import Any

from google.adk.agents import LlmAgent
from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter
from google.genai import types

from schemas.bounty import OPEN_STATUSES, BountyCheck, BountyStatus, SubmissionVerdict
from schemas.common import MediaStatus
from services import gemini
from services.armor_plugin import ModelArmorPlugin
from shared import fs, log
from shared.settings import (
    BOUNTY_MATCH_CONFIDENCE,
    BOUNTY_PARTIAL_FRACTION,
    GUEST_DAILY_POINTS_CAP,
    settings,
)

STAGE = "bounty_check"

#: How many submissions one tick will settle. A cap rather than a queue: a tick that spent ninety
#: seconds validating a backlog would miss its own cadence, and the backlog is still there next tick.
MAX_PER_TICK = 6

#: The aesthetic floor a fulfilment has to clear. Below it the photo is the right moment badly shot,
#: which is spec 05 §3's partial credit — the gap is genuinely still open. Not spec-pinned (HANDOFF §9).
QUALITY_FLOOR = 0.35

INSTRUCTION = """\
You validate one photo against one photo bounty at a live event. You are given the bounty's brief and
a structured description of the photo produced earlier by another agent that did look at it. You never
see the photo and you never decide who is in it.

Answer only whether the photo shows the moment the bounty asked for.

matchesMoment: true only if the described content is the requested moment or an unmistakable part of
it. A photo of the same people at the same event doing something else is false. A photo of the right
action from an unhelpful angle is still true.

confidence: how strongly the description supports that, 0.0 to 1.0. Be strict: a false fulfilment
closes a coverage gap that is still open.

reason: one short clause, quoting the evidence you used.
"""


@dataclass
class Settled:
    """What validation did this tick."""

    fulfilled: list[str] = field(default_factory=list)
    partial: list[str] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    awarded: int = 0
    checked: int = 0
    usage: gemini.ModelUsage = field(default_factory=gemini.ModelUsage)

    def as_report(self) -> dict[str, Any]:
        return {
            "checked": self.checked,
            "fulfilled": self.fulfilled,
            "partial": self.partial,
            "rejected": len(self.rejected),
            "pointsAwarded": self.awarded,
            "tokensIn": self.usage.tokensIn,
            "tokensOut": self.usage.tokensOut,
        }


@functools.lru_cache(maxsize=1)
def checker_agent() -> LlmAgent:
    """flash-lite, temperature 0, text only (spec 05 §3's "bounty-check step (flash-lite, structured)")."""
    return LlmAgent(
        name="bounty_validator",
        description="Scores one bounty submission against the bounty's brief.",
        model=gemini.adk_model(settings().model_classifier),
        instruction=INSTRUCTION,
        output_schema=BountyCheck,
        output_key="check",
        generate_content_config=types.GenerateContentConfig(temperature=0.0),
    )


# ---------------------------------------------------------------- the pass


async def settle(event_id: str, *, tick_id: str) -> Settled:
    """Validate every bounty submission that has finished the pipeline since the last tick."""
    settled = Settled()
    pending = _pending(event_id)
    if not pending:
        return settled

    for media in pending[:MAX_PER_TICK]:
        media_id = str(media.get("mediaId") or "")
        bounty_id = str(media.get("bountyId") or "")
        bounty = _bounty(event_id, bounty_id)
        if bounty is None or str(bounty.get("status") or "") not in OPEN_STATUSES:
            # The bounty expired or was already fulfilled while this photo was in the pipeline. The
            # photo is still a good photo and still in every album it belongs to; it just does not pay.
            _mark_checked(event_id, media_id, verdict="closed")
            settled.rejected.append(media_id)
            continue

        verdict, score, reason, usage = await _judge(event_id, media, bounty)
        settled.usage = settled.usage + usage
        settled.checked += 1

        awarded = _award(
            event_id,
            bounty_id=bounty_id,
            media_id=media_id,
            uid=str(media.get("uploaderUid") or ""),
            verdict=verdict,
            score=score,
            reason=reason,
        )
        settled.awarded += awarded
        _mark_checked(event_id, media_id, verdict=verdict.value)

        if verdict is SubmissionVerdict.FULFILLED:
            settled.fulfilled.append(bounty_id)
        elif verdict is SubmissionVerdict.PARTIAL:
            settled.partial.append(bounty_id)
        else:
            settled.rejected.append(media_id)

        log.line(
            "bounty_check",
            event_id=event_id,
            tick_id=tick_id,
            bounty=bounty_id,
            media_id=media_id,
            verdict=verdict.value,
            score=round(score, 2),
            points=awarded,
            reason=reason[:120],
        )
    return settled


def _pending(event_id: str) -> list[dict[str, Any]]:
    """Indexed media carrying a `bountyId` that no tick has judged yet.

    Filtered in Python on `bountyCheckedAt` rather than in the query: a bounty submission is a handful
    of documents at any moment, and a composite index on a field that is absent on 99.9% of the
    collection would be an index that exists to serve a query that returns almost nothing.
    """
    query = (
        fs.media_col(event_id)
        .where(filter=FieldFilter("status", "==", MediaStatus.INDEXED.value))
        .order_by("uploadedAt", direction=firestore.Query.DESCENDING)
        .limit(40)
    )
    found: list[dict[str, Any]] = []
    try:
        for snap in query.stream():
            doc = snap.to_dict() or {}
            if not doc.get("bountyId") or doc.get("bountyCheckedAt"):
                continue
            doc.setdefault("mediaId", snap.id)
            found.append(doc)
    except Exception as exc:  # noqa: BLE001 - a validation pass must not fail the tick
        log.warn("bounty_pending_query_failed", event_id=event_id, err=str(exc))
    return found


def _bounty(event_id: str, bounty_id: str) -> dict[str, Any] | None:
    if not bounty_id:
        return None
    snap = fs.bounty_ref(event_id, bounty_id).get()
    return (snap.to_dict() or {}) if snap.exists else None


async def _judge(
    event_id: str, media: dict[str, Any], bounty: dict[str, Any]
) -> tuple[SubmissionVerdict, float, str, gemini.ModelUsage]:
    """Deterministic checks first, then — only if they pass — the one contextual question."""
    curator = media.get("curator") or {}
    aesthetic = float(curator.get("aestheticScore") or 0.0)

    target_vip = bounty.get("targetVip")
    if target_vip:
        # Identity is the Face Indexer's answer, full stop. `albumOf` is the claimed-person membership
        # it maintains from a 512-d ArcFace match above τ_match (spec 03 §5.2).
        if str(target_vip) not in [str(p) for p in (media.get("albumOf") or [])]:
            return (
                SubmissionVerdict.REJECTED,
                0.0,
                f"{bounty.get('targetVipName') or 'the requested person'} is not in this photo",
                gemini.ModelUsage(),
            )

    target_stage = bounty.get("targetStage")
    if target_stage and curator.get("stageId") and curator.get("stageId") != target_stage:
        # A soft signal, not a veto: stage attribution is a fused posterior and can be wrong at a
        # boundary, so a stage mismatch costs the fulfilment but not the credit.
        return (
            SubmissionVerdict.PARTIAL if aesthetic >= QUALITY_FLOOR else SubmissionVerdict.REJECTED,
            0.3,
            f"looks like stage {curator.get('stageId')}, not {target_stage}",
            gemini.ModelUsage(),
        )

    target_moment = bounty.get("targetMoment")
    tags = [str(t) for t in (curator.get("momentTags") or [])]
    if target_moment and target_moment in tags:
        # The Curator already tagged it with the exact requested momentId (its instruction tells it to
        # reuse a listed id verbatim when it matches). There is nothing left to ask a model.
        verdict = (
            SubmissionVerdict.FULFILLED if aesthetic >= QUALITY_FLOOR else SubmissionVerdict.PARTIAL
        )
        return verdict, 1.0, f"tagged {target_moment} by the Curator", gemini.ModelUsage()

    if not target_moment:
        # A pure VIP bounty and the person is confirmed in frame: no moment to judge.
        verdict = (
            SubmissionVerdict.FULFILLED if aesthetic >= QUALITY_FLOOR else SubmissionVerdict.PARTIAL
        )
        return verdict, 1.0, "the requested person is in the photo", gemini.ModelUsage()

    try:
        check, usage = await gemini.run_structured(
            checker_agent(),
            [gemini.as_text_part(_prompt(media, bounty))],
            BountyCheck,
            stage=STAGE,
            plugins=[ModelArmorPlugin(surface="bounty_check", event_id=event_id)],
        )
    except gemini.ModelError as exc:
        # Conservative: no award, bounty stays open, the submission is judged again on no tick (it is
        # marked checked) but the guest keeps the photo. Paying out on a failed check would be the
        # only version of this that costs something irreversible.
        log.warn("bounty_check_failed", event_id=event_id, err=str(exc))
        return SubmissionVerdict.REJECTED, 0.0, "the validator could not judge this photo", exc.usage

    if check.matchesMoment and check.confidence >= BOUNTY_MATCH_CONFIDENCE:
        verdict = (
            SubmissionVerdict.FULFILLED if aesthetic >= QUALITY_FLOOR else SubmissionVerdict.PARTIAL
        )
    elif check.matchesMoment:
        verdict = SubmissionVerdict.PARTIAL
    else:
        verdict = SubmissionVerdict.REJECTED
    # `score` is the *match* score, so a rejection scores zero however sure the validator was of it.
    # The model returns confidence in its own judgment, and storing 0.95 next to `rejected` on the
    # submission record reads — to a host, and to anyone auditing an award — as the opposite verdict.
    score = float(check.confidence) if check.matchesMoment else 0.0
    return verdict, score, check.reason[:200], usage


def _prompt(media: dict[str, Any], bounty: dict[str, Any]) -> str:
    curator = media.get("curator") or {}
    return "\n".join(
        [
            "--- THE BOUNTY ---",
            f"title: {bounty.get('title')}",
            f"asked of guests: {bounty.get('copy')}",
            f"requested moment id: {bounty.get('targetMoment') or 'none'}",
            f"requested stage: {bounty.get('targetStage') or 'any'}",
            "",
            "--- THE PHOTO, AS THE CURATOR DESCRIBED IT ---",
            f"caption: {curator.get('caption') or '(none)'}",
            f"moment tags: {', '.join(str(t) for t in (curator.get('momentTags') or [])) or 'none'}",
            f"attributed stage: {curator.get('stageId') or 'unattributed'}",
            f"people in frame (estimate): {curator.get('peopleCountEstimate') or 0}",
            f"cultural elements: {', '.join(str(c) for c in (curator.get('culturalElements') or [])) or 'none'}",
        ]
    )


# ---------------------------------------------------------------- the award


@firestore.transactional
def _apply_award(
    transaction: firestore.Transaction,
    bounty_ref: firestore.DocumentReference,
    guest_ref: firestore.DocumentReference,
    *,
    media_id: str,
    uid: str,
    verdict: SubmissionVerdict,
    score: float,
    reason: str,
    now: dt.datetime,
) -> int:
    """One transaction: append the submission, award the points, close the bounty if fulfilled.

    Spec 05 §5's "no double-award under concurrent submissions" holds three ways over: the tick lease
    serialises ticks for an event, this transaction serialises within a tick, and the submission list
    is checked for `mediaId` so a replay is a no-op. Spec 05 §4's "duplicate submissions to one bounty
    keep only the best score" is implemented as a *difference*: a guest who already earned 60 on this
    bounty and now earns 150 receives 90, so their total is the best score and never the sum.
    """
    bounty_snap = bounty_ref.get(transaction=transaction)
    if not bounty_snap.exists:
        return 0
    bounty = bounty_snap.to_dict() or {}
    if str(bounty.get("status") or "") not in OPEN_STATUSES:
        return 0

    submissions = [s for s in (bounty.get("submissions") or []) if isinstance(s, dict)]
    if any(str(s.get("mediaId")) == media_id for s in submissions):
        return 0

    full = int(bounty.get("points") or 0)
    earned = {
        SubmissionVerdict.FULFILLED: full,
        SubmissionVerdict.PARTIAL: int(round(full * BOUNTY_PARTIAL_FRACTION)),
        SubmissionVerdict.REJECTED: 0,
    }[verdict]

    already = max(
        [int(s.get("points") or 0) for s in submissions if str(s.get("uid")) == uid] or [0]
    )
    payable = max(0, earned - already)

    guest_snap = guest_ref.get(transaction=transaction)
    guest = (guest_snap.to_dict() or {}) if guest_snap.exists else {}
    window_started = guest.get("pointsWindowStartedAt")
    window_total = int(guest.get("pointsWindowTotal") or 0)
    if not isinstance(window_started, dt.datetime) or (now - window_started) >= dt.timedelta(days=1):
        window_started, window_total = now, 0
    # Spec 05 §4's per-uid daily cap. Applied to the *award*, not to the leaderboard: points already
    # earned are never taken back, the next ones simply stop arriving.
    payable = max(0, min(payable, GUEST_DAILY_POINTS_CAP - window_total))

    submissions.append(
        {
            "mediaId": media_id,
            "uid": uid,
            "verdict": verdict.value,
            "score": round(float(score), 3),
            "points": earned,
            "reason": reason[:200],
            "at": now,
        }
    )
    updates: dict[str, Any] = {
        "submissions": submissions,
        "awardedTotal": int(bounty.get("awardedTotal") or 0) + payable,
        "lastSubmissionAt": now,
    }
    if verdict is SubmissionVerdict.FULFILLED:
        updates["status"] = BountyStatus.FULFILLED.value
        updates["fulfilledAt"] = now
        updates["fulfilledBy"] = uid
        updates["kioskTakeover"] = False
    transaction.set(bounty_ref, updates, merge=True)

    if payable and uid:
        transaction.set(
            guest_ref,
            {
                "uid": uid,
                "points": firestore.Increment(payable),
                "bountiesWon": firestore.Increment(1 if verdict is SubmissionVerdict.FULFILLED else 0),
                "pointsWindowStartedAt": window_started,
                "pointsWindowTotal": window_total + payable,
                "lastAwardAt": now,
            },
            merge=True,
        )
    return payable


def _award(
    event_id: str,
    *,
    bounty_id: str,
    media_id: str,
    uid: str,
    verdict: SubmissionVerdict,
    score: float,
    reason: str,
) -> int:
    try:
        return _apply_award(
            fs.db().transaction(),
            fs.bounty_ref(event_id, bounty_id),
            fs.guest_ref(event_id, uid or "unknown"),
            media_id=media_id,
            uid=uid,
            verdict=verdict,
            score=score,
            reason=reason,
            now=dt.datetime.now(dt.timezone.utc),
        )
    except Exception as exc:  # noqa: BLE001 - a failed award is retried next tick, not lost
        log.error("bounty_award_failed", event_id=event_id, bounty=bounty_id, err=str(exc))
        return 0


def _mark_checked(event_id: str, media_id: str, *, verdict: str) -> None:
    """Stamp the media so the next tick does not re-judge it. Not `visibility`, not a stage — a
    bounty verdict is metadata about a *submission*, and nothing about exposure depends on it."""
    try:
        fs.media_ref(event_id, media_id).update(
            {"bountyCheckedAt": fs.SERVER_TIMESTAMP, "bountyVerdict": verdict}
        )
    except Exception as exc:  # noqa: BLE001
        log.warn("bounty_mark_failed", event_id=event_id, media_id=media_id, err=str(exc))
