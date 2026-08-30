"""The media document — the per-item state machine (spec 03 §3).

Every field is declared here even though the perception sessions (B2) are what fill most of
them, because this document *is* the contract between intake, three workers, both directors,
the publisher and every frontend surface. `visibility` appears here for completeness only:
exactly one function is allowed to write it (spec 04 §2), and it is not this session's.
"""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, Field

from .common import (
    BoundingBox,
    ConsentRing,
    GuardianVerdict,
    MediaKind,
    MediaStatus,
    SceneSetting,
    StageState,
    StageTiming,
    Usage,
    Visibility,
)


class Consent(BaseModel):
    """Captured at selection time, per batch, with per-photo override later (spec 02 §4)."""

    ring: ConsentRing = ConsentRing.EVENT_POOL


class Quality(BaseModel):
    blur: float | None = None
    exposure: float | None = None
    eyesClosed: float | None = None


class CuratorBlock(BaseModel):
    """Curator output (spec 03 §5.1). `stagePosterior` is the deterministic post-LLM fusion of
    the visual distribution with the temporal prior — the LLM never sees the final answer."""

    stageId: str | None = None
    stagePosterior: dict[str, float] = Field(default_factory=dict)
    visual: dict[str, float] = Field(default_factory=dict)  # raw pre-fusion distribution
    momentTags: list[str] = Field(default_factory=list)
    aestheticScore: float = 0.0
    quality: Quality = Field(default_factory=Quality)
    isHighlight: bool = False
    caption: str | None = None
    culturalElements: list[str] = Field(default_factory=list)
    peopleCountEstimate: int | None = None
    #: Coerced to the closed vocabulary by `workers/curate/app.py::_scene_setting` — the model returns
    #: a bare string so one bad value cannot fail the whole parse. `shared/coverage.py` counts these
    #: per stage, and that accumulated count is the world model's hard layer.
    sceneSetting: SceneSetting = SceneSetting.UNKNOWN
    needsReview: bool = False  # conservative default on permanent failure (spec 03 §6)


class GuardianBlock(BaseModel):
    """SafeSearch hard gate + dignity rubric (spec 03 §5.3). Refusal defaults to host_review."""

    verdict: GuardianVerdict | None = None
    reasons: list[str] = Field(default_factory=list)
    safeSearch: dict[str, str] = Field(default_factory=dict)
    hostDecision: GuardianVerdict | None = None  # host override, audited
    decidedBy: str | None = None


class FaceRef(BaseModel):
    """Denormalised onto the media doc so a gallery grid renders without an extra read."""

    faceId: str
    box: BoundingBox
    personId: str | None = None
    clusterId: str | None = None


class Stages(BaseModel):
    """Parallel stage flags. `thumb`/`video_prep` are mutually exclusive by media kind."""

    thumb: StageState | None = None
    video_prep: StageState | None = None
    curate: StageState | None = None
    faces: StageState | None = None
    safety: StageState | None = None


class MediaDoc(BaseModel):
    mediaId: str
    uploaderUid: str
    batchId: str
    kind: MediaKind
    contentType: str
    size: int
    bountyId: str | None = None
    #: The first file of a selection. Its classify hop takes the priority queue so every uploader
    #: gets one photo on the wall fast even while a large batch drains behind it (spec 09 §2's
    #: `priority-queue`; the reasoning is in `intake/app.py::_dispatch`).
    batchLead: bool = False

    consent: Consent = Field(default_factory=Consent)
    subjectVetoes: list[str] = Field(default_factory=list)  # personIds who opted out of public

    status: MediaStatus = MediaStatus.AWAITING_UPLOAD
    rejectedReason: str | None = None
    duplicateOf: str | None = None  # exact-content dupe: skips perception, never public
    exifMissing: bool = False
    deleted: bool = False

    stages: Stages = Field(default_factory=Stages)
    attempts: dict[str, int] = Field(default_factory=dict)
    stageTimings: dict[str, StageTiming] = Field(default_factory=dict)
    usage: Usage = Field(default_factory=Usage)

    curator: CuratorBlock | None = None
    guardian: GuardianBlock | None = None
    faces: list[FaceRef] = Field(default_factory=list)
    albumOf: list[str] = Field(default_factory=list)  # personIds — maintained by the Face Indexer

    #: Derived. Written by `recompute_visibility` only (spec 04 §2) — never by a worker directly.
    visibility: Visibility | None = None

    capturedAt: dt.datetime | None = None  # EXIF DateTimeOriginal, event-timezone-interpreted
    uploadedAt: dt.datetime | None = None
    createdAt: dt.datetime | None = None  # single-field index disabled (spec 09 §3)

    gcsUri: str | None = None
    objectGeneration: int | None = None
    md5Hash: str | None = None
    thumbUri: str | None = None
    classifyUri: str | None = None
    displayUri: str | None = None
    posterUri: str | None = None
    proxyUri: str | None = None
    #: Video only (spec 03 §4): the 1 fps keyframe grid, capped at `MAX_KEYFRAMES`, in time order.
    #: This is what the Curator and the Guardian actually look at for a clip — the poster alone would
    #: make a 60-second video's verdict rest on one arbitrary instant — and what the Face Indexer runs
    #: detection over. Empty for a photo.
    keyframeUris: list[str] = Field(default_factory=list)
    width: int | None = None
    height: int | None = None
    #: Video only. `durationSec` is what bounds every per-clip cost in the system (keyframe count,
    #: proxy transcode time); `hasAudio` is recorded because nothing in this build screens sound, so a
    #: clip with audio carries an unscreened channel and that fact should be on the document rather
    #: than only in a spec footnote.
    durationSec: float | None = None
    hasAudio: bool | None = None
