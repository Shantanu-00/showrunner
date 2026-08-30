"""Enums and small value types shared across every document schema.

These names are the contract: `frontend/src/lib/types.ts` mirrors them, and Firestore stores
the string values verbatim. Changing a value here is a data migration, not a rename.
"""

from __future__ import annotations

import datetime as dt
from enum import Enum

from pydantic import BaseModel, Field


class ConsentRing(int, Enum):
    """Spec 02 §4. Ring 1 is the default — nothing is public by accident."""

    SELF_ONLY = 0
    EVENT_POOL = 1
    PUBLIC = 2


class Visibility(str, Enum):
    """Derived, never client-set. Only `recompute_visibility` writes it (spec 04 §2)."""

    SELF = "self"
    POOL = "pool"
    PUBLIC = "public"


class MediaKind(str, Enum):
    PHOTO = "photo"
    VIDEO = "video"


class MediaStatus(str, Enum):
    """Spec 03 §3. `indexed` is derived: it means every stage reached `done`."""

    AWAITING_UPLOAD = "awaiting_upload"
    UPLOADED = "uploaded"
    PROCESSING = "processing"
    INDEXED = "indexed"
    REJECTED = "rejected"
    QUARANTINED = "quarantined"
    ABANDONED = "abandoned"


class StageState(str, Enum):
    """`failed_permanent` exists so a poisoned input costs one pass, never a retry storm."""

    PENDING = "pending"
    DONE = "done"
    FAILED = "failed"
    FAILED_PERMANENT = "failed_permanent"


class Stage(str, Enum):
    """Stage flags complete in any order — the state machine is parallel, not linear."""

    THUMB = "thumb"
    VIDEO_PREP = "video_prep"
    CURATE = "curate"
    FACES = "faces"
    SAFETY = "safety"


class GuardianVerdict(str, Enum):
    """Spec 03 §5.3. `blocked` forces Ring 0 regardless of consent."""

    PUBLIC_OK = "public_ok"
    PRIVATE_ONLY = "private_only"
    HOST_REVIEW = "host_review"
    BLOCKED = "blocked"


class SceneSetting(str, Enum):
    """Where a frame was taken, as a closed vocabulary. The world model's only new observation.

    Lives here rather than in `curator_out.py` because four things share it: the Curator's output, the
    stored `CuratorBlock`, the host-declared `EventStage.expectedSetting`, and the kiosk's `onTopic`
    ranking term. A vocabulary with four readers is a `common.py` enum, same as `GuardianVerdict`.

    **Why it is closed.** The accumulated distribution of these values *is* the world model — the count
    of `indoor_venue` against `outdoor_nature` across an event is what tells the system, without being
    told, whether it is at a ballroom or on a hillside. An open vocabulary makes that distribution
    uncountable: `indoor`, `inside`, `hall` and `banquet_hall` are four keys for one fact, and the
    arithmetic silently stops meaning anything. This is the same lesson `culturalElements` taught —
    which is why `workers/curate/app.py` filters that against a host-reviewed glossary rather than
    trusting the prompt.

    **Why `closeup_detail` and `unknown` both exist.** They are not the same answer, and neither is a
    failure. A ring shot filling the frame has no visible setting; a dim crowd shot might have one the
    model cannot make out. Both must be *sayable*, because forcing either into a real setting is what
    would pollute the distribution — and both are treated as "no information" downstream rather than as
    evidence against the photo. Penalising a frame for carrying no setting would be punishing the
    absence of evidence, which is the mistake `workers/curate/fusion.py` avoids by flattening the
    temporal prior to 0.5 when EXIF is missing instead of scoring it 0.

    **Why `screen_or_document` is worth a slot of its own.** A screenshot, a slide or a photographed
    page is the one case that is off-topic at *every* event regardless of venue, so it needs no
    distribution to identify. It is the cheapest true positive the world model gets.
    """

    INDOOR_VENUE = "indoor_venue"
    OUTDOOR_VENUE = "outdoor_venue"
    OUTDOOR_NATURE = "outdoor_nature"
    DOMESTIC_INTERIOR = "domestic_interior"
    VEHICLE = "vehicle"
    STREET = "street"
    CLOSEUP_DETAIL = "closeup_detail"
    SCREEN_OR_DOCUMENT = "screen_or_document"
    UNKNOWN = "unknown"


#: The two values that carry no information about *where* the photo was taken, as opposed to carrying
#: information that happens to be unusual. Every consumer treats these as "no opinion" — see the
#: `SceneSetting` docstring, and `onTopic` in `publisher/program.py`.
UNINFORMATIVE_SETTINGS = frozenset(
    {SceneSetting.CLOSEUP_DETAIL.value, SceneSetting.UNKNOWN.value}
)


class StageTiming(BaseModel):
    """Stamped by each worker; the Flight Deck reads these as stage latencies (spec 10)."""

    queuedAt: dt.datetime | None = None
    startedAt: dt.datetime | None = None
    doneAt: dt.datetime | None = None


class Usage(BaseModel):
    """Summed Gemini usage per media item — feeds the cost ticker and the spec 11 §1.4 ceiling."""

    tokensIn: int = 0
    tokensOut: int = 0


class BoundingBox(BaseModel):
    """Face box in normalised [0,1] image coordinates, so it survives every render size."""

    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)
    w: float = Field(gt=0.0, le=1.0)
    h: float = Field(gt=0.0, le=1.0)
