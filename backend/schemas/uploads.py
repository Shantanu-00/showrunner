"""Upload API request/response bodies (spec 01 §3).

The client sends `consent: {public, selfOnly}` because that is the shape of the UI toggle it
collected; the server converts it once, here, into the canonical `ConsentRing` that every
downstream gate reads. Two representations of consent in the database would be a trust bug.
"""

from __future__ import annotations

import datetime as dt

from pydantic import BaseModel, Field, field_validator

from shared.settings import MAX_FILES_PER_CALL
from shared.ulid import is_ulid

from .common import ConsentRing, MediaKind


class BatchConsent(BaseModel):
    """`selfOnly` wins over `public` if a malformed client sends both."""

    public: bool = False
    selfOnly: bool = False

    @property
    def ring(self) -> ConsentRing:
        if self.selfOnly:
            return ConsentRing.SELF_ONLY
        if self.public:
            return ConsentRing.PUBLIC
        return ConsentRing.EVENT_POOL  # the default: pool, never public by accident


class UploadFileRequest(BaseModel):
    clientMediaId: str
    fileName: str = Field(max_length=512)
    contentType: str
    size: int = Field(gt=0)
    capturedAt: dt.datetime | None = None

    @field_validator("clientMediaId")
    @classmethod
    def _ulid(cls, v: str) -> str:
        if not is_ulid(v):
            raise ValueError("clientMediaId must be a ULID")
        return v


class UploadsRequest(BaseModel):
    batchId: str
    consent: BatchConsent = Field(default_factory=BatchConsent)
    bountyId: str | None = None
    files: list[UploadFileRequest] = Field(min_length=1, max_length=MAX_FILES_PER_CALL)

    @field_validator("batchId")
    @classmethod
    def _ulid(cls, v: str) -> str:
        if not is_ulid(v):
            raise ValueError("batchId must be a ULID")
        return v


class UploadTarget(BaseModel):
    """One issued upload slot. Photos get `signedUrl`; videos get `resumableSessionUri`
    (a bearer token — the client persists it in the outbox and we never log it)."""

    mediaId: str
    kind: MediaKind
    signedUrl: str | None = None
    resumableSessionUri: str | None = None
    objectPath: str
    expiresAt: dt.datetime | None = None


class UploadsResponse(BaseModel):
    uploads: list[UploadTarget]
    ring: ConsentRing  # echoed back so the UI can show the padlock state it actually got
    bountyId: str | None = None  # None if the requested bounty was not active (dropped silently)


class RefreshUrlResponse(BaseModel):
    upload: UploadTarget
