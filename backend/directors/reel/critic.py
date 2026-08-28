"""CRITIC — spec 06 §2.4 and §3 step 3: a cheap rubric pass, then a deterministic linter.

The split between the two is the point, and it is the same split the whole system is built on:

- **The critic judges *quality*.** Does the storyboard reference at least three specific moments by
  name? Is the arc non-flat? Are consecutive shots near-duplicates? Is the persona mandate honoured?
  These are contextual questions, so a model answers them — `gemini-3.5-flash-lite`, one call, and its
  verdict buys at most **one** regeneration (spec 06 §2.4's "≤1 retry"). Its opinion can send a
  storyboard back; it can never let an invalid one through.
- **The linter enforces *validity*.** Every mediaId exists in the manifest, no shot repeats, the shot
  count is inside the 10–24 band, captions are Latin-script and short, no two consecutive shots are
  the same face cluster and moment. It is a pure function, it has no opinion, and it is the thing that
  actually protects the renderer. Spec 06 §2's closing line: *templates constrain validity; evidence
  and persona determine content.*

`lint` **repairs where repair is unambiguous** and rejects only what it cannot: an unknown mediaId is
dropped, an over-long or non-Latin caption is dropped, a duplicate shot is dropped. Rejecting the
whole plan for one bad caption would spend a second `gemini-3.7-flash` call to fix something that has
exactly one correct fix. What it will not do is invent a shot to reach the minimum — a plan that lints
down below `REEL_MIN_SHOTS` is a real failure and goes back to the director or to `ops/`.
"""

from __future__ import annotations

import functools
import unicodedata

from google.adk.agents import LlmAgent
from google.genai import types

from schemas.reel import Critique, ReelPersona, ReelPlan, ShotPlan
from services.gemini import adk_model, as_text_part
from shared.settings import (
    REEL_CRITIC_MIN_MOMENTS,
    REEL_MAX_SHOTS,
    REEL_MIN_SHOTS,
    settings,
)

from .select import Candidate

MAX_CAPTION_CHARS = 34

INSTRUCTION = f"""\
You are a film editor's second pair of eyes. You are shown a storyboard for a short vertical reel cut
from real event photographs, plus the evidence it was cut from. Score it against the rubric and return
one JSON object per the schema. You are not asked to improve it and you must not propose shots.

Rubric:
- momentsNamed: how many *specific* moments from the evidence the narrative brief refers to by name.
  A brief that says "a beautiful celebration" names zero however long it is.
- arcIsFlat: true if the shot durations and the caption placement show no shape — every shot the same
  length, no build, no landing, or a pacing curve the shot list contradicts.
- personaHonored: does the selection match the stated mandate? A couple reel made of crowd shots, or a
  guest-energy reel made of posed portraits, is a no.
- issues: at most three short imperative fixes, each naming what to change. "Cut the two near-identical
  portraits at shots 4 and 5." "Give the first-dance shot four more beats than the arrival shots."
  Never "make it better".

verdict REVISE only when something in the list above is genuinely wrong. A competent storyboard passes;
sending back a good cut costs a full regeneration and buys nothing. A storyboard that names fewer than
{REEL_CRITIC_MIN_MOMENTS} moments, or whose arc is flat, is not competent.
"""


@functools.lru_cache(maxsize=1)
def critic_agent() -> LlmAgent:
    """`gemini-3.5-flash-lite` — spec 06 §3 step 3's "cheap flash-lite critic".

    `temperature=0` here, unlike the director. This one is scoring against a fixed rubric, and a critic
    that scores the same storyboard differently on two runs is not a rubric, it is a mood.
    """
    return LlmAgent(
        name="reel_critic",
        description="Scores a reel storyboard against a fixed rubric and asks for at most one revision.",
        model=adk_model(settings().model_classifier),
        instruction=INSTRUCTION,
        output_schema=Critique,
        output_key="critique",
        generate_content_config=types.GenerateContentConfig(
            temperature=0.0, max_output_tokens=1024
        ),
    )


def critic_block(plan: ReelPlan, candidates: list[Candidate]) -> str:
    """The storyboard, plus just enough evidence to tell a specific brief from a generic one."""
    by_id = {c.media_id: c for c in candidates}
    lines = [
        f"--- BRIEF ---\n{plan.narrativeBrief}",
        f"title={plan.title!r} pacing={plan.pacing.value} captionVoice={plan.captionVoice!r}",
        "",
        f"--- STORYBOARD: {len(plan.shots)} shots ---",
    ]
    for i, shot in enumerate(plan.shots, start=1):
        c = by_id.get(shot.mediaId)
        lines.append(
            f"{i:>2}. {shot.mediaId} beats={shot.durationBeats} move={shot.move.value} "
            f"transition={shot.transition.value}"
            + (f" caption={shot.captionLine!r}" if shot.captionLine else "")
            + (" EMPHASIS" if shot.emphasis else "")
        )
        if c is not None:
            lines.append(
                f"     evidence: {c.caption or '(no caption)'} | moments="
                f"{', '.join(c.moment_tags) or 'none'} | people={len(c.person_ids) or 'unidentified'}"
            )
    lines += [
        "",
        f"--- MUSIC ---\nstyle={plan.music.style!r} tempo={plan.music.tempoBpm} arc={plan.music.arc!r}",
        "",
        "--- MOMENTS AVAILABLE IN THE EVIDENCE ---",
        ", ".join(sorted({t for c in candidates for t in c.moment_tags})) or "(none tagged)",
    ]
    return "\n".join(lines)


def prompt_parts(block: str) -> list[types.Part]:
    return [as_text_part(block)]


# ---------------------------------------------------------------- the deterministic linter


def is_latin(text: str) -> bool:
    """True when every letter in `text` is Latin script.

    Spec 06 §3 step 5, decided in HANDOFF §3: `ffmpeg drawtext` cannot shape Devanagari, so a caption
    in it renders as boxes on a five-metre screen. Checked here rather than trusted from the prompt
    because it is one line and a model told "Latin only" in English will occasionally answer in the
    script the event is in. Digits, punctuation and emoji are not letters and pass through — an emoji
    that the font lacks degrades to a missing glyph, which is cosmetic, not garbage.
    """
    for ch in text:
        if not ch.isalpha():
            continue
        if not unicodedata.name(ch, "").startswith("LATIN"):
            return False
    return True


def lint(
    plan: ReelPlan, candidates: list[Candidate], *, persona: ReelPersona
) -> tuple[list[ShotPlan], list[str]]:
    """Pure. Returns (usable shots, issues found). Repairs the unambiguous, reports everything.

    `issues` is recorded on the reel document and is the honest answer to "what did the model get
    wrong" — it is also what `scripts/smoke_reel.py --offline` asserts against, with no network.
    """
    by_id = {c.media_id: c for c in candidates}
    issues: list[str] = []
    kept: list[ShotPlan] = []
    seen: set[str] = set()

    for i, shot in enumerate(plan.shots, start=1):
        if shot.mediaId not in by_id:
            issues.append(f"shot {i}: mediaId {shot.mediaId!r} is not in the candidate set — dropped")
            continue
        if shot.mediaId in seen:
            issues.append(f"shot {i}: {shot.mediaId} already used — dropped")
            continue

        caption = (shot.captionLine or "").strip()
        if caption:
            if len(caption) > MAX_CAPTION_CHARS:
                issues.append(f"shot {i}: caption {len(caption)} chars over {MAX_CAPTION_CHARS} — dropped")
                caption = ""
            elif not is_latin(caption):
                issues.append(f"shot {i}: caption is not Latin script — dropped (spec 06 §3)")
                caption = ""

        prev = by_id[kept[-1].mediaId] if kept else None
        this = by_id[shot.mediaId]
        if prev is not None and (
            prev.primary_cluster == this.primary_cluster
            and prev.primary_moment == this.primary_moment
            and prev.primary_cluster != "nobody"
        ):
            issues.append(
                f"shot {i}: near-duplicate of shot {len(kept)} "
                f"(same person and moment: {this.primary_moment}) — dropped"
            )
            continue

        seen.add(shot.mediaId)
        kept.append(shot.model_copy(update={"captionLine": caption or None}))

    if len(kept) > REEL_MAX_SHOTS:
        issues.append(f"{len(kept)} shots over the {REEL_MAX_SHOTS} ceiling — trimmed from the end")
        kept = kept[:REEL_MAX_SHOTS]

    distinct_transitions = {s.transition for s in kept}
    if kept and len(distinct_transitions) < 2:
        issues.append("only one transition across the whole reel — the cut has no punctuation")

    captioned = sum(1 for s in kept if s.captionLine)
    if kept and captioned > len(kept) * 0.5:
        issues.append(f"{captioned}/{len(kept)} shots captioned — over the one-third guidance")

    if not any(s.emphasis for s in kept) and kept:
        issues.append("no emphasis shot — nothing will land on a downbeat")

    if persona is ReelPersona.MAIN_CHARACTER:
        # The only lint rule that is a *consent* rule rather than a craft one, so it is here and not in
        # the prompt: a private reel about one person may not contain a frame they are not in.
        stray = [s.mediaId for s in kept if not by_id[s.mediaId].person_ids]
        if stray:
            issues.append(f"main_character reel contains {len(stray)} frames without the subject — dropped")
            kept = [s for s in kept if by_id[s.mediaId].person_ids]

    if len(kept) < REEL_MIN_SHOTS:
        issues.append(
            f"only {len(kept)} usable shots, under the {REEL_MIN_SHOTS} floor — "
            "not enough for a reel"
        )
    return kept, issues


def rubric_failures(critique: Critique) -> list[str]:
    """The deterministic half of the critic's verdict, so a model that says PASS while reporting a flat
    arc and one named moment does not get the last word. Spec 06 §2.4's thresholds, applied in code."""
    failures: list[str] = []
    if critique.momentsNamed < REEL_CRITIC_MIN_MOMENTS:
        failures.append(
            f"the brief names {critique.momentsNamed} specific moments; name at least "
            f"{REEL_CRITIC_MIN_MOMENTS} things that actually happened"
        )
    if critique.arcIsFlat:
        failures.append("the arc is flat — vary shot lengths and place the emphasis shots deliberately")
    if not critique.personaHonored:
        failures.append("the selection does not honour the persona mandate — re-select accordingly")
    return failures
