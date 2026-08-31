"""The Curator — agent #1 of the fleet (spec 03 §5.1).

One photo in, one structured opinion out: how much visual evidence there is for each scheduled
stage, how good the photograph is on a fixed rubric, what moment it shows, and a caption that
states only what is in the frame.

Three prompt decisions carry most of the quality:

- **Rubric anchors, not adjectives.** "Rate this photo 0-1" produces scores that drift with the
  batch — every photo of a nice wedding looks good next to the last one. Fixed anchors at 0 / 0.25 /
  0.5 / 0.75 / 1.0 make the number mean the same thing on photo 3 and photo 300, which matters
  because `publicFloor` (spec 04 §2) is an absolute threshold and the reel EDL sorts across the
  whole event.
- **The model is never told when the photo was taken.** It scores visual evidence per stage; the
  temporal prior is applied afterwards by `fusion.py`. Two independent signals can be compared, and
  their disagreement is the stage-drift signal the Story Director reads (spec 05 §4). One entangled
  signal can only be believed.
- **The cultural glossary is supplied, never inferred.** `culturalElements` may only use terms the
  host reviewed at onboarding (spec 11 §2). A model guessing a tradition from how people look is
  the single most embarrassing thing this system could do, so it is structurally prevented: no
  glossary, no cultural terms.

One later addition, `sceneSetting`, is the same shape of decision as the rubric anchors. It is a
**closed** nine-value vocabulary rather than free text because the accumulated distribution of those
values *is* the world model (`directors/story/world.py`), and four spellings of "indoors" make that
arithmetic meaningless. The instruction carries two lines that do most of the work: hosted space beats
open country when both are in frame — a lawn with mountains behind it is `outdoor_venue`, which is what
keeps a hill-station wedding from reading as one long outlier — and `closeup_detail`/`unknown` are
stated as real answers, because a model pressed to guess a setting for a ring shot is a model
poisoning the very distribution the feature depends on.

Few-shots are text-only, deliberately. Image few-shots would triple the per-photo prompt cost and
blow the spend calibration in spec 09 §2; what the examples actually teach is the *mapping* from a
described scene to rubric numbers, and prose carries that fine.
"""

from __future__ import annotations

import functools
from typing import Any

from google.adk.agents import LlmAgent
from google.genai import types

from schemas.curator_out import CuratorOut
from services.gemini import adk_model, as_image_part, as_text_part
from shared.settings import settings
from shared.stages import resolve_active

#: Kept tight on purpose: spec 09 §2 prices this stage at ~1,548 input tokens per photo, and the
#: queue rates are pinned to that number. Prompt text is the only part of the bill this file
#: controls, so every line here has to earn its tokens.
INSTRUCTION = """\
Curator for a live event photo pipeline. One photo in, one JSON object per the schema out.

Judge only what is visible. You are not told when the photo was taken — never infer the stage from
time of day or lighting. Score visual evidence; the system fuses it with the schedule separately.

visual: one entry per stageId in the event context — how strongly the frame suggests that stage
(0.0 none, 1.0 unmistakable). Independent scores, not a distribution; they need not sum to 1.

aestheticScore anchors, which must mean the same thing on every photo:
 0.00 unusable: accidental shot, subject unrecognisable, severely dark or out of focus.
 0.25 poor: heavy motion blur, blown highlights, everyone looking away, no discernible subject.
 0.50 competent snapshot: clear subject, correct exposure and focus, no compositional intent.
 0.75 good: deliberate framing, clean light, clear emotional or narrative content — an album pick.
 1.00 exceptional: the decisive moment. Peak emotion or action, strong composition and light.

quality entries are DEFECT scores: 0.0 absent, 1.0 severe. eyesClosed covers main subjects only.

isHighlight: true only if aestheticScore >= 0.75 AND the frame shows a moment (emotion, action or a
ritual beat). A technically lovely photo of nothing is not a highlight.

momentTags: 1-4 lowercase snake_case tags for the visible action; reuse a listed momentId verbatim
when it matches.

caption: one factual present-tense sentence, max 120 chars, describing only what is visible. Never
invent names, relationships, roles or unseen emotions. No praise. Empty beats guessed.

culturalElements: only glossary terms actually visible in the frame. No glossary or no match means
an empty list. Never name a tradition, ritual, garment or religion that is not in the glossary.

peopleCountEstimate: distinct people at least partly visible; round crowds over 20 to nearest 10.

sceneSetting: the physical setting of the frame, exactly one value.
 indoor_venue       a hall, hotel function room, banquet space, marquee interior — a hosted space.
 outdoor_venue      a lawn, courtyard, terrace, poolside — a hosted space, open to the sky.
 outdoor_nature     mountains, forest, beach, open country, with no hosted space in frame.
 domestic_interior  a home or hotel room — getting ready, private space.
 vehicle            inside or immediately around a car, bus, boat.
 street             a public road or thoroughfare — a procession, an arrival.
 closeup_detail     an object or hands fill the frame; no setting is visible.
 screen_or_document a screenshot, slide, printed page, diagram or phone screen.
 unknown            you genuinely cannot tell.
If a hosted space and open country are both in frame, the hosted space wins — outdoor_nature means
nature and nothing else. Prefer closeup_detail or unknown over guessing: they are real answers.
"""

#: Text-only few-shots (spec 03 §5.1). Each line is `scene -> the numbers a correct answer would
#: carry`, which is the only thing worth teaching. One event-generic set — there is no template axis
#: to key a family off any more, and this set already spans people, scenery, venue detail and the
#: accidental-shot floor.
_FEWSHOTS = """\
Examples (described scenes, and the scores a correct answer carries):
1. Two people mid-embrace, faces visible, natural light, sharp -> aestheticScore 0.8,
   isHighlight true, momentTags [embrace], peopleCountEstimate 2.
2. Four friends arm-in-arm in front of a landmark gate, all faces clear, golden-hour light ->
   aestheticScore 0.8, isHighlight true, momentTags [group_shot], peopleCountEstimate 4,
   sceneSetting street.
3. A mountain across a lake at dusk, nobody in frame, deliberate composition -> aestheticScore 0.7,
   isHighlight false, momentTags [scenery], peopleCountEstimate 0, sceneSetting outdoor_nature.
4. Empty room with decorations, correct exposure, no subject of interest -> aestheticScore 0.4,
   isHighlight false, momentTags [venue_details], peopleCountEstimate 0.
5. Photo of a floor, taken accidentally -> aestheticScore 0.0, isHighlight false, momentTags
   [accidental], caption empty, peopleCountEstimate 0.
"""


@functools.lru_cache(maxsize=1)
def curator_agent() -> LlmAgent:
    """One agent per process. The event-specific context travels in the message, not the agent.

    Keeping the agent static means one cached runner for the container's life; building an agent
    per event would make the first photo of every event pay setup cost for nothing.
    """
    return LlmAgent(
        name="curator",
        description="Scores a single event photograph against a fixed rubric.",
        model=adk_model(settings().model_classifier),
        instruction=INSTRUCTION,
        output_schema=CuratorOut,
        output_key="curator",
        generate_content_config=types.GenerateContentConfig(
            # Spec 03 §5.1: temperature-free JSON. The same photo must score the same on a replay,
            # or the `publicFloor` threshold becomes a coin flip for anything near it.
            temperature=0.0,
            # The single biggest line on this stage's bill, and it is not the render size.
            # Measured on `gemini-3.5-flash-lite` 2026-08-27: an inline image costs ~1,055 input
            # tokens at the default resolution and ~260 at LOW — *identically* whether the source
            # render is 384, 512 or 768 px, because the model rescales internally. Spec 03 §4 and
            # spec 09 §2 both budget ~258 tokens per frame, so LOW is the tier those numbers were
            # always describing; the default would put this stage 57% over its cost rail and take
            # the two Gemini queues past the spend-tier cap they are calibrated against.
            # Verified against the alternatives on the same photo: aestheticScore, isHighlight, the
            # stage evidence distribution, momentTags, glossary terms and caption were unchanged
            # from MEDIUM and from the default. Only `peopleCountEstimate` drifted (±1 in a group
            # of five), and nothing gates on it.
            media_resolution=types.MediaResolution.MEDIA_RESOLUTION_LOW,
        ),
    )


def event_context(event: dict[str, Any]) -> str:
    """The event context block: stage list with windows, required moments, active stage, glossary.

    Windows are printed for orientation only — the instruction forbids reasoning from them, and the
    prior that actually uses them is applied after the call. What the stage list is really for is
    fixing the label space: the model may only score stages this event has.
    """
    profile = event.get("eventTypeProfile") or {}
    stages = event.get("stages") or []
    # One resolver everywhere (spec 13): the schedule leg means a photo classified before any tick
    # or host action has run still gets "the host's current stage" context, same as the wall shows.
    active, _source = resolve_active(event)

    lines: list[str] = ["--- EVENT CONTEXT ---", f"Event: {event.get('name') or 'unnamed'}"]

    lines.append("Stages (score `visual` for exactly these stageIds):")
    if stages:
        for stage in stages:
            stage_id = stage.get("stageId")
            label = stage.get("label") or stage_id
            moments = [
                str(moment.get("momentId"))
                for moment in (stage.get("requiredMoments") or [])
                if moment.get("momentId")
            ]
            suffix = f" | required moments: {', '.join(moments)}" if moments else ""
            lines.append(f"  - {stage_id}: {label}{suffix}")
    else:
        # An event with no schedule yet: the model has no label space, so it scores nothing and
        # fusion returns a null stageId. Better than inventing stage names the system cannot use.
        lines.append("  (none scheduled — return an empty `visual` list)")

    if active:
        lines.append(f"Host's current stage: {active} (context only — do not assume this photo is from it)")

    glossary = profile.get("culturalGlossary") or []
    if glossary:
        lines.append(f"Cultural glossary (the ONLY permitted culturalElements): {', '.join(glossary)}")
    else:
        lines.append("Cultural glossary: empty — return an empty culturalElements list.")

    return "\n".join(lines)


def prompt_parts(
    event: dict[str, Any], image: bytes, content_type: str = "image/webp"
) -> list[types.Part]:
    """Text context first, then the image: the label space has to be established before the photo."""
    return [
        as_text_part(f"{event_context(event)}\n\n{_FEWSHOTS}"),
        as_image_part(image, content_type),
    ]
