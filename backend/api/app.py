"""`api` service — the only surface guests talk to directly.

Its whole job in this session is spec 01's first design principle: **register intent before
bytes.** A media doc exists, with its uploader and consent already attached, before a single
byte leaves the phone. That makes orphans detectable, consent unambiguous, and the pipeline
metadata-complete before perception starts.

Bytes then go phone → GCS directly (signed URL), never through this service.
"""

from __future__ import annotations

import datetime as dt

import os

from fastapi import Depends, FastAPI, Path
from fastapi.middleware.cors import CORSMiddleware

from schemas.event import EventStatus, UPLOAD_OPEN_STATUSES
from shared import fs, log
from shared.auth import Principal, caller
from shared.settings import settings

from .host import create_router as host_create_router, router as host_router
from .identity import claim_router, router as identity_router
from .internal import router as internal_router
from .moderation import router as moderation_router
from .uploads import router as uploads_router

log.configure("api")

app = FastAPI(title="Showrunner API", version="0.1.0", docs_url=None, redoc_url=None)

# The PWA is served from Firebase Hosting (and localhost during development), so the API needs
# explicit origins. Bucket CORS is configured separately in deploy/buckets.sh — the browser
# PUTs bytes straight to GCS, which is a different origin policy entirely.
_origins = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins or ["http://localhost:3000"],
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
    max_age=3600,
)

app.include_router(uploads_router)
app.include_router(host_create_router)
app.include_router(host_router)
app.include_router(identity_router)
app.include_router(claim_router)
app.include_router(moderation_router)
# Cloud Scheduler's target (spec 09 §2). Not under /v1: it is infrastructure calling infrastructure,
# and it authenticates its caller itself because `api` is the one service deployed public.
app.include_router(internal_router)


@app.get("/livez")
async def livez() -> dict[str, str]:
    return {"status": "ok", "service": "api", "environment": settings().environment}


@app.get("/v1/events/{eventId}/public")
async def event_public(
    eventId: str = Path(min_length=1, max_length=128),
    principal: Principal = Depends(caller),
) -> dict[str, object]:
    """The minimum an authenticated guest app needs to render the join screen, the gallery's
    stage chips, and the kiosk/PWA theme flip (spec 12 §3).

    Deliberately narrow: name, status, timezone, active stage, theme, a stage label list. No
    cost figures, no class, no demo flags, no stage timing/required moments, nothing that would
    leak platform or operational state to a guest.
    """
    event = fs.get_event(eventId)
    if not event:
        return {"exists": False}
    status = event.get("status", EventStatus.DRAFT.value)
    stages = event.get("stages") or []
    return {
        "exists": True,
        "eventId": eventId,
        "name": event.get("name"),
        "status": status,
        "timezone": event.get("timezone"),
        "activeStage": event.get("stageOverride") or event.get("activeStage"),
        "templateId": (event.get("eventTypeProfile") or {}).get("templateId"),
        "stages": [
            {"stageId": s.get("stageId"), "label": s.get("label"), "theme": s.get("theme")}
            for s in stages
        ],
        "uploadsOpen": status in {s.value for s in UPLOAD_OPEN_STATUSES},
        "publicFrozen": bool(event.get("publicFrozen")),
        "serverTime": dt.datetime.now(dt.timezone.utc),
    }
