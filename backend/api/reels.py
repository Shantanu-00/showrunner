"""Reel endpoints — the video redirect the kiosk plays, and the host's commission button.

`GET …/reels/{reelId}/video` is the load-bearing one, and it is worth explaining why it exists at all
rather than the reel document simply carrying a URL to the file.

Every bucket in this project has `--public-access-prevention` (deploy/buckets.sh), which is correct: a
publicly-readable object stays readable by anyone holding the URL *after* spec 06 §7 unpublishes the
reel, so opening the curated bucket would make the consent interlock a UI behaviour rather than an
enforced one. The two remaining options are a signed URL stored on the document, and a redirect. A stored
signed URL expires while the document still advertises it — a reel that plays on demo day and 404s during
the judging month — so the document stores this path instead, and the exposure check happens on every
single request:

- the reel must exist, and its `visibility` must be `public` (i.e. `store.publish` re-validated every
  constituent and nothing has retracted since), **or** the caller must be the host;
- then, and only then, a 60-minute signed GET is minted and returned as a 302.

**It is unauthenticated for a published reel on an *open* event**, and that is the same decision
`kiosk/playlist` already carries (`allow read: if true`, spec 09 §3): a `<video>` element cannot send an
Authorization header, a kiosk is a television in a venue, and making the wall depend on an auth session
would be a way for it to go dark rather than a control. What makes it safe is that "published" is a
*derived* state — `recompute_visibility` and `store.publish` are the only writers — so this endpoint can
only ever serve something the trust rail already decided was public.

On an **invite-only** event (`access.mode == 'invite'`) that branch requires event membership instead, for
the reason `api/media.py` records at length: a reel is a montage of the same guests' photographs, and an
eventId plus a reelId must stop being enough the moment the host shuts the door. The venue TV gets there
through a kiosk link (`POST /v1/events/{eventId}/kiosk-links`), which grants `members` and nothing else.

**Downloading is not watching, and it is gated differently.** `?download=1` requires event membership on
*every* event, open ones included. Playback has to survive without a session because a `<video>` on a
kiosk cannot carry a header; a download produces a file that leaves this system for good, and spec 06
§7's consent interlock — which can pull a reel off every surface the instant a subject objects — has no
reach into somebody's camera roll. Guests of the event and its host can keep a copy. A passer-by holding
an eventId can watch the wall.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, Path, Query, Response
from fastapi.responses import RedirectResponse

from schemas.common import Visibility
from schemas.reel import ReelPersona, ReelStatus
from shared import errors, gcs, log
from shared.auth import Principal, caller, verify_bearer

from .membership import is_invite_only

router = APIRouter(prefix="/v1/events/{eventId}", tags=["reels"])

#: How long the signed URL a kiosk gets is good for. Long enough that a premiere cannot expire midway
#: through the show, short enough that a link scraped off the wire is worthless the same afternoon.
VIDEO_URL_TTL_MINUTES = 60


def _require_host(principal: Principal, event_id: str) -> None:
    if not (principal.is_host_of(event_id) or principal.platform_admin):
        raise errors.forbidden("HOST_ONLY", "this action requires the host")


def _reel(event_id: str, reel_id: str) -> dict[str, Any]:
    from directors.reel import store

    doc = store.get(event_id, reel_id)
    if doc is None:
        raise errors.not_found("REEL_NOT_FOUND", "no such reel")
    return doc


@router.get("/reels/{reelId}/video")
async def reel_video(
    eventId: str = Path(min_length=1, max_length=128),
    reelId: str = Path(min_length=1, max_length=64),
    download: bool = Query(False, description="serve as an attachment (the wrap panel's download)"),
    authorization: str | None = Header(default=None),
) -> Response:
    """302 to a short-lived signed URL for the rendered file. See the module docstring for why.

    `?download=1` (spec 13 §8) changes the signed URL's content-disposition to an attachment — and,
    unlike playback, **it always requires event membership**, on an open event as much as an
    invite-only one. The asymmetry is deliberate and it is a consent judgement, not a hardening
    reflex: *watching* is what a kiosk in a venue does, and a `<video>` element cannot send an
    Authorization header, so the play path has to be reachable without a session or the wall goes
    dark. *Keeping a copy* is a different act. A downloaded file leaves the consent interlock behind
    entirely — spec 06 §7 can retract a reel from every surface the moment somebody in it asks not to
    be shown, and it cannot reach into a stranger's camera roll. So the permanent copy is for the
    people who were actually there: the guests of this event and its host, verified against a real
    ID token, and nobody who merely came across an eventId.

    Every visibility re-check is otherwise identical, and the interlock still retracts a downloadable
    recap exactly as it retracts a playing one."""
    doc = _reel(eventId, reelId)
    published = (
        doc.get("status") == ReelStatus.PUBLISHED.value
        and doc.get("visibility") == Visibility.PUBLIC.value
    )
    if not published or download or is_invite_only(eventId):
        # A host may preview an unpublished or failed reel from the console; nobody else may. A
        # *published* reel additionally needs a member when the event is invite-only, or whenever a
        # permanent copy is being asked for. The token is only verified on this branch, so an open
        # event's kiosk playback still costs no auth round trip.
        try:
            principal = verify_bearer(authorization)
        except Exception:  # noqa: BLE001 - an absent or bad token on a private reel is simply a 404
            raise errors.not_found("REEL_NOT_AVAILABLE", "this reel is not available") from None
        if published:
            if not principal.is_member_of(eventId):
                raise errors.not_found("REEL_NOT_AVAILABLE", "this reel is not available")
        else:
            _require_host(principal, eventId)

    parsed = gcs.parse_gs_uri(str(doc.get("gcsUri") or ""))
    if parsed is None:
        raise errors.not_found("REEL_NOT_RENDERED", "this reel has no rendered file yet")

    bucket, path = parsed
    url = gcs.signed_get_url(
        bucket,
        path,
        ttl_minutes=VIDEO_URL_TTL_MINUTES,
        response_type="video/mp4",
        attachment_filename=f"showrunner-{reelId}.mp4" if download else None,
    )
    log.info("reel_video_served", event_id=eventId, reel_id=reelId, published=published)
    # 302 rather than 307: this is a GET and the redirect target is a different resource, and every
    # browser and every TV webview handles a 302 on a <video src> without a preflight.
    return RedirectResponse(url, status_code=302)


@router.post("/reels")
async def commission_reel(
    body: dict[str, Any],
    eventId: str = Path(min_length=1, max_length=128),
    principal: Principal = Depends(caller),
) -> dict[str, Any]:
    """Host-commissioned reel (spec 06 §1's "or host button").

    The host chooses a persona and, for a recap, a stage. Everything else — eligibility, the aesthetic
    floor, the VIP floor, the in-flight and daily caps — is decided by `directors/reel/commission.py`,
    identically to a director-initiated commission. A host can ask for a reel; a host cannot ask for a
    reel made of photographs that are not eligible for it.
    """
    _require_host(principal, eventId)
    from directors.reel import commission as reel_commission

    raw = str(body.get("persona") or ReelPersona.COUPLE.value).strip().lower()
    try:
        persona = ReelPersona(raw)
    except ValueError:
        raise errors.bad_request(
            "BAD_PERSONA",
            f"persona must be one of: {', '.join(p.value for p in ReelPersona)}",
        ) from None

    result = reel_commission.commission(
        eventId,
        persona=persona,
        stage_id=(str(body["stageId"]) if body.get("stageId") else None),
        person_id=(str(body["personId"]) if body.get("personId") else None),
        reason=str(body.get("reason") or "commissioned from the host console")[:300],
        commissioned_by="host",
    )
    if not result.ok:
        raise errors.conflict("COMMISSION_REFUSED", result.reason)
    return {"reelId": result.reel_id, "status": ReelStatus.DIRECTING.value, "note": result.reason}


@router.post("/reels/{reelId}/retry")
async def retry_reel(
    eventId: str = Path(min_length=1, max_length=128),
    reelId: str = Path(min_length=1, max_length=64),
    principal: Principal = Depends(caller),
) -> dict[str, Any]:
    """Re-run a failed commission. Resumable — the pipeline skips whatever already landed, so a retry
    after a render failure costs no second plan and no second Lyria clip."""
    _require_host(principal, eventId)
    from directors.reel import commission as reel_commission

    result = reel_commission.retry(eventId, reelId)
    if not result.ok:
        raise errors.conflict("RETRY_REFUSED", result.reason)
    return {"reelId": reelId, "restarted": True, "note": result.reason}
