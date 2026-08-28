"""One commission, end to end — spec 06 §3's six steps in order.

    SELECT → DIRECT → CRITIC → SCORE → EDL → RENDER → PUBLISH

It runs as one Cloud Run Job execution (`backend/render/main.py`). The alternative shape — a Cloud Run
service for steps 1–4 that then starts a job for step 5 — was rejected, and the reasoning is worth
recording because spec 06 §3 implies the split:

- **Cloud Tasks cannot start a Cloud Run Job.** Tasks speaks HTTP; a job is started through the Run
  Admin API. So the split costs an extra service, an extra service account, and an extra hop, and the
  EDL still has to be persisted on the reel document in between — which it does anyway.
- **The whole commission is then one traceable unit.** One execution, one log stream, one document
  walking `directing → composing → rendering → published`. When a reel does not appear, there is one
  place to look.
- **The 8 vCPU are idle for about forty seconds** while the model calls and the Lyria stream run. At
  spec 06 §6's budget that is under a cent, against a whole extra deployment surface.

**Resumable at stage granularity.** Every step's output is written to the reel document as it lands, and
each step is skipped when its output is already there. A retried commission that died in `render` does
not pay for a second `gemini-3.7-flash` plan or a second Lyria clip — it re-reads them and re-renders.
That is also what makes the retry safe to fire from a later tick.

**Failure asymmetry, following HANDOFF §4.15's discipline.** A reel is not a media item and has no
uploader waiting on it, so there is no quarantine analogue: every failure here marks the reel `failed`,
writes an `ops/` alert, and leaves every photograph and every other reel untouched. The one failure that
is *not* fatal is Lyria's — a silent reel is a reel (see `music.py`).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

from schemas.reel import (
    Critique,
    CriticVerdict,
    KenBurnsMove,
    ReelPersona,
    ReelPlan,
    ReelStatus,
    ShotPlan,
)
from services import gemini
from services.armor_plugin import ModelArmorPlugin
from shared import fs, gcs, log
from shared.settings import (
    REEL_CRITIC_PASS_SCORE,
    REEL_MIN_SHOTS,
    REEL_RENDER_COST_USD,
    settings,
)

from . import agent, critic, edl as edl_mod, music, render as render_mod, select, store

STAGE = "reel"


@dataclass
class Report:
    """What one commission did. Printed by the job and asserted by `scripts/smoke_reel.py`."""

    reel_id: str
    persona: str
    status: str
    shots: int = 0
    candidates: int = 0
    direct_attempts: int = 0
    critique_score: float | None = None
    lint_issues: list[str] = field(default_factory=list)
    tempo: float | None = None
    beat_error_ms: float | None = None
    duration: float | None = None
    silent: bool = False
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    published: bool = False
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v not in (None, [], 0, 0.0, False)} | {
            "reel_id": self.reel_id,
            "persona": self.persona,
            "status": self.status,
        }


async def run(event_id: str, reel_id: str) -> Report:
    """Produce one commissioned reel. Returns a report; never raises past the `ops/` alert."""
    started = dt.datetime.now(dt.timezone.utc)
    doc = store.get(event_id, reel_id)
    if doc is None:
        raise RuntimeError(f"reel {reel_id} does not exist on event {event_id}")

    persona = ReelPersona(doc.get("persona", ReelPersona.COUPLE.value))
    report = Report(reel_id=reel_id, persona=persona.value, status=str(doc.get("status")))
    usage = gemini.ModelUsage()

    try:
        event = fs.get_event(event_id) or {}
        audience_ring = int(doc.get("audienceRing", 2))
        stage_id = doc.get("stageId")
        person_id = doc.get("personId")
        version = int(doc.get("version", 1))
        seed = agent.style_seed(event_id, persona, version)

        # ---------------------------------------------------------------- 1. SELECT
        store.progress(event_id, reel_id, 5, status=ReelStatus.DIRECTING)
        candidates, names = select.fetch(
            event_id,
            persona=persona,
            audience_ring=audience_ring,
            person_id=person_id,
            stage_id=stage_id,
        )
        report.candidates = len(candidates)
        if len(candidates) < REEL_MIN_SHOTS:
            # Not a failure of the director — there is genuinely not enough material. Said plainly,
            # because "the system degrades by event size because the control loop is evidence-driven"
            # (HANDOFF §4.19) applies to reels exactly as it applies to bounties.
            reason = (
                f"only {len(candidates)} eligible photographs; a reel needs at least {REEL_MIN_SHOTS}"
            )
            store.fail(event_id, reel_id, reason, alert=False)
            report.status = ReelStatus.FAILED.value
            report.error = reason
            log.info("reel_insufficient_material", event_id=event_id, reel_id=reel_id, n=len(candidates))
            return report

        store.patch(
            event_id,
            reel_id,
            candidateCount=len(candidates),
            candidateSnapshotAt=fs.SERVER_TIMESTAMP,
            styleSeed=seed,
            progress=15,
        )

        # ---------------------------------------------------------------- 2/3. DIRECT + CRITIC
        plan, shots, critique, attempts, issues, direct_usage = await _direct_and_critique(
            event_id,
            event=event,
            persona=persona,
            candidates=candidates,
            names=names,
            seed=seed,
            stage_id=stage_id,
            person_id=person_id,
        )
        usage = usage + direct_usage
        report.direct_attempts = attempts
        report.lint_issues = issues
        report.critique_score = critique.score if critique else None

        if len(shots) < REEL_MIN_SHOTS:
            reason = f"storyboard linted down to {len(shots)} usable shots: {'; '.join(issues[:3])}"
            store.fail(event_id, reel_id, reason)
            report.status = ReelStatus.FAILED.value
            report.error = reason
            return report

        store.patch(
            event_id,
            reel_id,
            status=ReelStatus.COMPOSING.value,
            title=plan.title[:120] or "Untitled",
            narrativeBrief=plan.narrativeBrief[:2000],
            pacing=plan.pacing.value,
            captionVoice=plan.captionVoice[:200],
            music=plan.music.model_dump(),
            critique=(critique.model_dump() if critique else None),
            directAttempts=attempts,
            lintIssues=issues[:20],
            progress=30,
        )

        # ---------------------------------------------------------------- 4. SCORE
        score = music.compose(plan.music, seed=seed)
        report.silent = score.silent
        report.tempo = score.tempo
        music_uri = None
        if score.audio:
            music_uri = gcs.upload_bytes(
                settings().curated_bucket,
                f"{event_id}/reels/{reel_id}.mp3",
                score.audio,
                content_type=score.mime or "audio/mpeg",
                cache_control="public, max-age=31536000, immutable",
            )
        store.patch(
            event_id,
            reel_id,
            musicUri=music_uri,
            musicCaption=(score.caption[:1000] or None),
            tempoBpm=round(score.tempo, 2),
            beatCount=len(score.beats),
            failureReason=score.failure,
            progress=40,
        )

        # ---------------------------------------------------------------- 5. EDL
        cut = edl_mod.build(
            shots,
            candidates,
            curve=plan.pacing,
            beats=score.beats,
            downbeats=score.downbeats,
            music_duration=score.duration,
        )
        if len(cut.shots) < REEL_MIN_SHOTS:
            reason = f"EDL produced {len(cut.shots)} shots: {'; '.join(cut.notes[:3])}"
            store.fail(event_id, reel_id, reason)
            report.status = ReelStatus.FAILED.value
            report.error = reason
            return report

        by_id = {c.media_id: c for c in candidates}
        fitted = [
            edl_mod.framing(by_id[s.mediaId], _move_of(shots, s.mediaId)).mode == "fit"
            for s in cut.shots
        ]
        manifest = [s.mediaId for s in cut.shots]
        report.shots = len(cut.shots)
        report.beat_error_ms = cut.beat_error_ms

        store.patch(
            event_id,
            reel_id,
            status=ReelStatus.RENDERING.value,
            shots=[s.model_dump() for s in cut.shots],
            assetManifest=manifest,
            durationSec=cut.duration,
            lintIssues=(issues + cut.notes)[:20],
            progress=45,
        )

        # ---------------------------------------------------------------- 6. RENDER
        rendered = render_mod.run(
            event_id,
            reel_id,
            shots=cut.shots,
            candidates=candidates,
            audio=score.audio,
            fitted=fitted,
        )
        report.duration = rendered.duration
        report.cost_usd = round(score.cost_usd + REEL_RENDER_COST_USD, 4)

        store.patch(
            event_id,
            reel_id,
            gcsUri=rendered.gcs_uri,
            videoUri=render_mod.playable_url(event_id, reel_id),
            sizeBytes=rendered.size_bytes,
            durationSec=rendered.duration,
            usage={"tokensIn": usage.tokensIn, "tokensOut": usage.tokensOut},
            costUsd=report.cost_usd,
        )

        # ---------------------------------------------------------------- 7. PUBLISH
        report.published = store.publish(
            event_id, reel_id, manifest=manifest, audience_ring=audience_ring
        )
        report.status = (
            ReelStatus.PUBLISHED.value if report.published else ReelStatus.UNPUBLISHED.value
        )
        report.tokens_in, report.tokens_out = usage.tokensIn, usage.tokensOut
        return report

    except Exception as exc:  # noqa: BLE001 - a commission failing is an alert, never a crash loop
        reason = f"{type(exc).__name__}: {exc}"
        log.error("reel_pipeline_failed", event_id=event_id, reel_id=reel_id, err=reason[:400])
        store.fail(event_id, reel_id, reason)
        report.status = ReelStatus.FAILED.value
        report.error = reason[:400]
        report.tokens_in, report.tokens_out = usage.tokensIn, usage.tokensOut
        return report
    finally:
        log.line(
            "reel",
            event_id=event_id,
            reel_id=reel_id,
            persona=report.persona,
            status=report.status,
            shots=report.shots or None,
            candidates=report.candidates or None,
            attempts=report.direct_attempts or None,
            tempo=(round(report.tempo, 1) if report.tempo else None),
            beat_err_ms=report.beat_error_ms,
            silent=report.silent or None,
            tokens_in=report.tokens_in or None,
            tokens_out=report.tokens_out or None,
            err=report.error,
            ms=int((dt.datetime.now(dt.timezone.utc) - started).total_seconds() * 1000),
        )


def _move_of(shots: list[ShotPlan], media_id: str) -> KenBurnsMove:
    return next((s.move for s in shots if s.mediaId == media_id), KenBurnsMove.PUSH_IN)


async def _direct_and_critique(
    event_id: str,
    *,
    event: dict[str, Any],
    persona: ReelPersona,
    candidates: list[select.Candidate],
    names: dict[str, str],
    seed: int,
    stage_id: str | None,
    person_id: str | None,
) -> tuple[ReelPlan, list[ShotPlan], Critique | None, int, list[str], gemini.ModelUsage]:
    """DIRECT, then CRITIC, then at most one regeneration (spec 06 §2.4's "≤1 retry").

    The critic's verdict is combined with `critic.rubric_failures` — its own reported numbers, applied
    deterministically — so a model that answers PASS while admitting a flat arc and one named moment
    does not get the last word. That is the same shape as the Guardian's gate: the model reports
    observations, code decides what they mean.
    """
    usage = gemini.ModelUsage()
    plan: ReelPlan | None = None
    shots: list[ShotPlan] = []
    issues: list[str] = []
    critique: Critique | None = None
    feedback: list[str] = []
    previous_ids: list[str] = []
    attempts = 0

    for attempt in (1, 2):
        attempts = attempt
        block = agent.evidence_block(
            event=event,
            persona=persona,
            candidates=candidates,
            names=names,
            seed=seed + attempt - 1,
            stage_id=stage_id,
            person_id=person_id,
            critique=feedback or None,
            previous_shot_ids=previous_ids or None,
        )
        plan, plan_usage = await gemini.run_structured(
            agent.direct_agent(),
            agent.prompt_parts(block),
            ReelPlan,
            stage=STAGE,
            # The prompt is assembled from text that entered the system by several routes — the host's
            # cultural glossary, the event name, the Curator's captions of guest photographs — and the
            # regeneration pass feeds the model's own previous output back in. One guard in front of the
            # call beats a check at each of those places (the reasoning `directors/story/director.py`
            # records for the same plugin).
            plugins=[ModelArmorPlugin(surface="reel", event_id=event_id)],
        )
        usage = usage + plan_usage

        shots, issues = critic.lint(plan, candidates, persona=persona)
        previous_ids = [s.mediaId for s in shots]

        if attempt == 2 or len(shots) < REEL_MIN_SHOTS:
            break

        critique, critic_usage = await gemini.run_structured(
            critic.critic_agent(),
            critic.prompt_parts(critic.critic_block(plan, candidates)),
            Critique,
            stage=f"{STAGE}_critic",
        )
        usage = usage + critic_usage

        feedback = list(critique.issues[:3]) + critic.rubric_failures(critique)
        wants_revision = (
            critique.verdict is CriticVerdict.REVISE
            or critique.score < REEL_CRITIC_PASS_SCORE
            or bool(critic.rubric_failures(critique))
        )
        if not wants_revision:
            break
        log.info(
            "reel_critic_revise",
            event_id=event_id,
            score=round(critique.score, 2),
            moments=critique.momentsNamed,
            issues=len(feedback),
        )

    assert plan is not None  # the loop always runs at least once and run_structured raises otherwise
    return plan, shots, critique, attempts, issues, usage
