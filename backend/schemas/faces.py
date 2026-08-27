"""Face documents, and the internal embedding call that `api` makes into `worker-face`.

`events/{eventId}/faces/{faceId}` is spec 03 §1's face record. Its `embedding` field is stored as
a Firestore `Vector`, not as a JSON array, so it is absent from `FaceDoc` on purpose: this model
describes the part of the document that anything other than the vector index reads.

`faceId` is deliberately *not* a fresh ULID. It is `{mediaId}-{NN}` over faces sorted
largest-box-first, which makes re-indexing a photo overwrite the same documents instead of
doubling every album — Cloud Tasks is at-least-once and spec 03 §6 requires a re-run to overwrite
identically. The mediaId prefix keeps the ULID's write-spreading property.
"""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, Field

from .common import BoundingBox


class FaceDoc(BaseModel):
    faceId: str
    mediaId: str
    box: BoundingBox
    #: Set only by a ≥ τ_match match against an enrolled selfie, or by an audited claim.
    personId: str | None = None
    #: Incremental threshold clustering (τ_cluster) — the "Person 7" grouping for people who have
    #: not enrolled. Always present; duplicates are tolerated and reconciled (spec 03 §5.2).
    clusterId: str | None = None
    claimId: str | None = None  # the claimAudits entry that linked this face, if any

    detScore: float = 0.0  # detector confidence
    matchScore: float | None = None  # similarity to the matched person — stored for calibration
    clusterScore: float | None = None  # similarity to the neighbour whose cluster was adopted

    createdAt: dt.datetime | None = None
    claimedAt: dt.datetime | None = None


class FaceDetection(BaseModel):
    """One detected face: normalised box, unit-norm 512-d embedding, detector confidence."""

    box: BoundingBox
    embedding: list[float] = Field(min_length=512, max_length=512)
    detScore: float = 0.0


class EmbedRequest(BaseModel):
    """`POST /embed` on `worker-face` — the selfie path (spec 02 §3).

    `api` cannot embed a selfie itself: InsightFace + ONNX Runtime is a 1 GB image with a ~12 s
    model load, and `api` scales 0→10 on 512 MB. So the model lives in exactly one container and
    the enrollment endpoint calls it over authenticated, private Cloud Run (IAM-gated to `sa-api`
    and `sa-tasks`). One model, one place it can drift.
    """

    image: str  # base64, no data: prefix
    maxFaces: int = 1


class EmbedResponse(BaseModel):
    faces: list[FaceDetection] = Field(default_factory=list)
