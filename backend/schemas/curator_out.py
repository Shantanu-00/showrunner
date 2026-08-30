"""What the Curator model is allowed to return (spec 03 §5.1).

Deliberately *not* the same shape as `media.CuratorBlock`. This is the LLM's contract; the stored
block is the LLM's output **plus** two fields the model never sees: `stageId` and `stagePosterior`.
Stage attribution is a deterministic fusion of the model's visual opinion with the event's
timetable (§5.1, implemented in `workers/curate/fusion.py`), so asking the model for the final
answer would both invite it to guess at the timetable and make the fusion unauditable.

Two hard-won constraints on this file, both from the friction log (2026-08-27):

**No free-form maps.** A `dict[str, float]` field in a response schema comes back empty every time —
the response-schema dialect has no open-ended map, so the field is silently dropped rather than
rejected. Anything distribution-shaped is a list of fixed-key objects, converted to a dict in code.

**No prose in `description=`.** The schema is billed on every single request, and roughly twice over
(ADK sends it as `response_schema` *and* as a JSON instruction), which made it the second-largest
line item on this stage's prompt after the image itself. Every rule the model needs is stated once
in `workers/curate/agent.py::INSTRUCTION`, which is billed once; explanation for humans belongs in
docstrings, which are never billed. Field names carry the rest.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class VisualStageScore(BaseModel):
    """One stage's visual likelihood. A list of these is the distribution.

    `stageId` must be one of the ids listed in the prompt's event context block; fusion drops
    anything else, since a stage the event does not have has no temporal prior to fuse with.
    """

    stageId: str
    score: float = Field(ge=0.0, le=1.0)


class QualityOut(BaseModel):
    """Technical defects, scored independently of taste — 0.0 = absent, 1.0 = severe.

    `exposure` covers both over- and under-exposure: the pipeline only ever asks "is this photo's
    exposure a problem", never in which direction.
    """

    blur: float = Field(ge=0.0, le=1.0, default=0.0)
    exposure: float = Field(ge=0.0, le=1.0, default=0.0)
    eyesClosed: float = Field(ge=0.0, le=1.0, default=0.0)


class CuratorOut(BaseModel):
    """The Curator's judgment on one photo (or one video's poster + keyframe grid).

    Every field is optional with a conservative default, so a partial answer degrades to a low
    score rather than to a validation failure and a wasted retry.
    """

    visual: list[VisualStageScore] = Field(default_factory=list)
    momentTags: list[str] = Field(default_factory=list)
    aestheticScore: float = Field(ge=0.0, le=1.0, default=0.0)
    quality: QualityOut = Field(default_factory=QualityOut)
    isHighlight: bool = False
    caption: str = ""
    culturalElements: list[str] = Field(default_factory=list)
    peopleCountEstimate: int = Field(ge=0, default=0)
    #: One `SceneSetting` value. Deliberately typed `str` and **not** the enum: a response schema that
    #: rejects an unrecognised value fails the *whole* parse, and `services/gemini.py::run_structured`
    #: allows exactly one retry before declaring the answer permanently unusable — so one hallucinated
    #: setting would cost a photo its entire classification, including the aesthetic score and the
    #: caption that were probably fine. `workers/curate/app.py::_scene_setting` coerces it against the
    #: enum and falls back to `unknown`, which is the same posture `_glossary_filter` takes for
    #: `culturalElements`: let the model answer freely, enforce the vocabulary in code.
    sceneSetting: str = "unknown"
