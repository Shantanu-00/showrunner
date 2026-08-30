"""The Guardian's dignity rubric — agent #2 of the fleet (spec 03 §5.3, pass 2).

The Curator asks "is this a good photograph". This asks a different and harder question: "would the
person in this frame want it on a five-metre screen in front of their family". No rule table answers
that, because the same image is a highlight at one moment and a violation at another — tears during
Kanyadaan are the photograph of the day; the same face crying alone by the bar is nobody's business.
That distinction is why this is an agent and not a threshold, and the stage context in the prompt is
the entire mechanism.

Three prompt decisions carry the behaviour:

- **It reports observations, it does not set policy.** The verdict it returns is a proposal;
  `gate.py` combines it with the SafeSearch floor, the `minor_prominent` rule and the host's declared
  dials. The instruction says so explicitly, because a model told it has the final say starts
  hedging toward the middle, and the middle here is `host_review` for everything — a review queue
  nobody can clear is the same as no safety system at all.
- **The host's dials are quoted to it as the host's own words**, never as a cultural assumption
  (spec 11 §2). This prompt template is identical for a Hindu wedding, a bachelor party and a
  corporate offsite; only the declared dials and the glossary differ. That is the whole reason the
  same shot type reads `public_ok` at one event and `host_review` at another.
- **The default is stated as the conservative one.** "When you cannot tell, return host_review" is
  cheaper than any post-hoc correction, and it matches what `gate.py` does with a refusal anyway.
"""

from __future__ import annotations

import functools
from typing import Any

from google.adk.agents import LlmAgent
from google.genai import types

from schemas.guardian_out import GuardianOut
from services.gemini import adk_model, as_image_part, as_text_part
from shared.settings import settings
from shared.stages import resolve_active

#: Spec 09 §2 budgets this stage the same ~1,548 input tokens as the Curator, and the safety queue's
#: 8/s rate is calibrated against it. Prompt text is the only part of that bill this file controls.
INSTRUCTION = """\
Guardian for a live event photo pipeline. You judge dignity, not content categories: a separate
classifier already handled explicit material and its answer is final. One photo in, one JSON object
per the schema out.

You propose; you do not decide. The system combines your verdict with that classifier's result, the
host's declared sensitivity settings and a hard rule about children. Report what you see and give
your honest reading of it.

verdict:
 public_ok    — safe and kind on a big screen in front of family and colleagues.
 private_only — fine for the people in it, wrong for a public screen.
 host_review   — you cannot tell, or someone's dignity plausibly turns on context you lack.
When genuinely unsure, return host_review. Never guess public_ok.

reasons — only from this list, only when actually visible:
 eyes_closed              main subject's eyes shut or mid-blink.
 mid_bite                 eating, chewing, mouth full.
 wardrobe_risk            clothing slipped, strap down, unintended exposure.
 distress_out_of_context  genuine unhappiness that the moment does not explain.
 unflattering_angle       angle or expression that mocks rather than portrays.
 minor_prominent          a child is a main subject of the frame.
 pda_visible              kissing, embracing romantically, intimate contact.
 alcohol_visible          alcohol being drunk, held, or poured.
 attire_revealing         notably more revealing than everyday formal wear.
The last three are observations for the host's own settings to act on — report them plainly,
without judgment, and do not let them alone drive your verdict.

ritualEmotion: true when strong emotion in the frame is part of the occasion (a ritual farewell, a
speech, vows) rather than private upset. This is the distinction that matters most: ritual tears are
the best photograph of the day; a guest crying alone is private_only.

note: one short factual sentence for the host, only if the verdict is not public_ok. No names, no
speculation about relationships, no praise.
"""

#: Short, text-only, and all of them are about the same axis — the one the rubric cannot express
#: as a threshold: witnessed ceremonial emotion versus private upset, at any kind of event. Image
#: few-shots would triple the per-photo prompt cost (spec 09 §2).
_FEWSHOTS = """\
Examples (described scenes, and the answer a correct reading gives):
1. A father in tears during a ceremony as he gives his daughter's hand away, family gathered
   around, everyone attentive -> verdict public_ok, reasons [], ritualEmotion true.
2. Friends crying and laughing at once during a farewell toast on the last night of a trip, arms
   around each other -> verdict public_ok, reasons [], ritualEmotion true.
3. A guest sitting alone at the edge of the room, face in hands, nobody else in frame ->
   verdict private_only, reasons [distress_out_of_context], ritualEmotion false.
4. Toddler in the foreground holding a plate, adults blurred behind ->
   verdict public_ok, reasons [minor_prominent] (the system routes this to the host on its own).
"""


@functools.lru_cache(maxsize=1)
def guardian_agent() -> LlmAgent:
    """One agent per process; the event's context travels in the message, not in the agent."""
    return LlmAgent(
        name="guardian",
        description="Judges whether a single event photograph is kind to the people in it.",
        model=adk_model(settings().model_classifier),
        instruction=INSTRUCTION,
        output_schema=GuardianOut,
        output_key="guardian",
        generate_content_config=types.GenerateContentConfig(
            # Same reasoning as the Curator (spec 03 §5.1): a replay must reach the same verdict, or
            # a photo near a boundary flips exposure on a coin toss.
            temperature=0.0,
            # Measured 2026-08-27 on `gemini-3.5-flash-lite`: an inline image costs ~1,055 input
            # tokens at the default `media_resolution` and ~260 at LOW, *regardless of render size*.
            # LOW is the tier spec 03 §4 and spec 09 §2 always meant by "~258 tokens/frame"; the
            # default would put the two Gemini queues past the spend cap they are calibrated to.
            media_resolution=types.MediaResolution.MEDIA_RESOLUTION_LOW,
        ),
    )


def _stage_context(event: dict[str, Any], media: dict[str, Any]) -> list[str]:
    """What moment this photo belongs to, and how confidently we know it.

    The three perception stages run in parallel from three queues (spec 03 §2), so the Curator's
    `stageId` may or may not exist yet when this runs. Rather than serialise the stages — which
    would double the pipeline's latency to buy one prompt line — the context degrades honestly: the
    Curator's classification when it has landed, the host's current stage otherwise, labelled either
    way so the model knows how much to lean on it.
    """
    stages = {
        str(stage.get("stageId")): stage
        for stage in (event.get("stages") or [])
        if stage.get("stageId")
    }
    curator_stage = ((media.get("curator") or {}).get("stageId")) or None
    host_stage, _source = resolve_active(event)  # one resolver everywhere (spec 13)

    stage_id = curator_stage or host_stage
    if not stage_id:
        return ["Moment: unknown — judge the frame on its own terms."]

    stage = stages.get(str(stage_id)) or {}
    label = stage.get("label") or stage_id
    moments = [
        str(moment.get("label") or moment.get("momentId"))
        for moment in (stage.get("requiredMoments") or [])
        if moment.get("momentId")
    ]
    source = (
        "classified from the photo itself"
        if curator_stage
        else "the host's current stage (this photo may be from another)"
    )
    lines = [f"Moment: {label} — {source}."]
    if moments:
        lines.append(f"Expected at this moment: {', '.join(moments)}.")
    return lines


def _dial_context(event: dict[str, Any]) -> list[str]:
    """The host's declared sensitivity settings, quoted as theirs (spec 11 §2).

    Phrased as what the host said rather than as an instruction, because the deterministic clamp in
    `gate.py` is what enforces them. The model needs them as *context* — a `private_only` PDA dial
    tells it this host would rather not see an embrace on the wall, which legitimately colours a
    borderline reading — not as a rule it is responsible for applying.
    """
    profile = (event.get("eventTypeProfile") or {}).get("sensitivityProfile") or {}
    pda = str(profile.get("pda") or "context_dependent")
    alcohol = str(profile.get("alcohol") or "context_dependent")
    attire = str(profile.get("attire") or "standard")
    return [
        "The host declared, for their own event:",
        f"  public affection: {pda} · alcohol: {alcohol} · attire expectations: {attire}",
        "  (These are the host's settings, not assumptions about anyone's culture. Report what you",
        "   see; the system applies them.)",
    ]


def event_context(event: dict[str, Any], media: dict[str, Any]) -> str:
    lines = ["--- EVENT CONTEXT ---", f"Event: {event.get('name') or 'unnamed'}"]
    lines += _stage_context(event, media)
    lines += _dial_context(event)

    glossary = (event.get("eventTypeProfile") or {}).get("culturalGlossary") or []
    if glossary:
        # Ritual vocabulary the host reviewed. Its only job here is to let the model recognise that a
        # named ritual moment explains strong emotion — never to name a tradition it cannot see.
        lines.append(f"Rituals the host named for this event: {', '.join(glossary)}")
    return "\n".join(lines)


def prompt_parts(
    event: dict[str, Any], media: dict[str, Any], image: bytes, content_type: str = "image/webp"
) -> list[types.Part]:
    """Context first, then the photo — the moment has to be established before the frame."""
    return [
        as_text_part(f"{event_context(event, media)}\n\n{_FEWSHOTS}"),
        as_image_part(image, content_type),
    ]
