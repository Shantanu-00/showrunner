"""The wall's ambient soundtrack — one endpoint, same exposure reasoning as the reel video.

`GET …/ambience` hands a kiosk a short-lived signed URL for the Lyria bed that matches what the wall is
currently showing, composing it the first time a given mood is seen (`publisher/ambience.py`).

Three decisions worth stating, because they are the same three the reel video endpoint had to make:

**It is unauthenticated on an open event.** An `<audio>` element cannot send an Authorization header,
and a kiosk is a television in a venue — the same reasoning that makes `kiosk/playlist` world-readable
(spec 09 §3) and a published reel's bytes reachable without a session. What is being served is also
categorically less sensitive than either: a generated instrumental clip contains no photograph, no face
and no name. On an **invite-only** event it still requires membership, because the *mood* is derived from
the event's own aggregates and a track's caption describes the room.

**The redirect, not a stored URL.** The curated bucket has `--public-access-prevention` and a signed URL
stored on a document expires while the document still advertises it. Same 302, same reason.

**A first request may legitimately fail, and says so rather than blocking.** Lyria takes tens of seconds.
The first caller for a mood claims the composition transactionally and does the work; anyone arriving
meanwhile gets `202` with a `retryAfterSec`, which the kiosk treats as "no music yet" and retries. It
never gets a spinner and it never gets an error dialog — a wall with no music is a wall, and that is the
worst case this endpoint has.
"""

from __future__ import annotations

from fastapi import APIRouter, Header, Path, Query, Response
from fastapi.responses import JSONResponse, RedirectResponse

from shared import errors, gcs, log
from shared.auth import verify_bearer

from .membership import is_invite_only

router = APIRouter(prefix="/v1/events/{eventId}", tags=["ambience"])

#: Matches the reel video's TTL for the same reason: long enough that an hour of wall time never sees a
#: track expire mid-loop, short enough that a scraped link is worthless the same afternoon.
AMBIENCE_URL_TTL_MINUTES = 60

#: What a kiosk is told to wait when someone else is already composing this mood.
COMPOSING_RETRY_SEC = 20


@router.get("/ambience")
async def event_ambience(
    eventId: str = Path(min_length=1, max_length=128),
    json: bool = Query(False, description="return {url} instead of a 302 — the browser-fetch path"),
    authorization: str | None = Header(default=None),
) -> Response:
    """The ambient track for this wall right now. 302 to a signed MP3, or `?json=1` for the URL."""
    from publisher import ambience as ambience_mod
    from shared import fs

    event = fs.get_event(eventId)
    if not event:
        raise errors.not_found("EVENT_NOT_FOUND", "no such event")

    if is_invite_only(eventId):
        # The mood is derived from this event's own aggregates, so it is the event's information.
        try:
            principal = verify_bearer(authorization)
        except Exception:  # noqa: BLE001 - an absent or bad token on a private event is simply a 404
            raise errors.not_found("AMBIENCE_NOT_AVAILABLE", "not available") from None
        if not principal.is_member_of(eventId):
            raise errors.not_found("AMBIENCE_NOT_AVAILABLE", "not available")

    result = ambience_mod.ensure(eventId, event=event)
    if not result.ok:
        # 202 for "come back", 204 for "there will not be one". Neither is an error the wall shows a
        # human: `useAmbience` reads both as silence and, for 202, retries once.
        if result.status == "composing":
            return JSONResponse(
                {"status": "composing", "moodKey": result.mood_key, "retryAfterSec": COMPOSING_RETRY_SEC},
                status_code=202,
                headers={"Retry-After": str(COMPOSING_RETRY_SEC)},
            )
        log.info("ambience_unavailable", event_id=eventId, reason=result.reason[:160])
        return Response(status_code=204)

    parsed = gcs.parse_gs_uri(str(result.gcs_uri or ""))
    if parsed is None:
        return Response(status_code=204)
    bucket, path = parsed
    url = gcs.signed_get_url(
        bucket, path, ttl_minutes=AMBIENCE_URL_TTL_MINUTES, response_type="audio/mpeg"
    )
    if json:
        return JSONResponse(
            {
                "url": url,
                "expiresInSec": AMBIENCE_URL_TTL_MINUTES * 60,
                "moodKey": result.mood_key,
                "tempoBpm": result.tempo_bpm,
                "durationSec": result.duration_sec,
                "caption": result.caption,
            }
        )
    return RedirectResponse(url, status_code=302)
