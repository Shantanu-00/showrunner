"""`GET …/media/{mediaId}/render` — the signed-URL redirect every `<img>` on the guest and kiosk
surfaces plays through, for the same reason `api/reels.py` exists: every bucket in this project
has `--public-access-prevention` (deploy/buckets.sh), so `thumbUri`/`displayUri`'s stored
`gs://` value can never load directly in a browser, and opening the derived bucket would turn the
3-ring consent model into a UI convention rather than an enforced one — an unguessable-ULID
object would stay fetchable by anyone holding the URL after a subject veto or a ring change.

The exposure check is the same one `firestore.rules`'s `media/{mediaId}` match already runs
(host/admin, the uploader, `isPubliclyVisible()`, `isSubject()`), re-implemented here rather than
imported because rules and Python cannot share code — and re-checked on every request rather than
once, so a retraction between two page loads actually revokes the bytes.

**The public branch is unauthenticated only on an *open* event.** That was once unconditional, for the
same reason the reel video's is: an `<img src>` cannot carry an Authorization header and a kiosk is a
television in a venue. But it also meant an eventId plus a mediaId returned photo bytes to anybody,
which is what made "private event" a label over an open door — the rules boundary (`isMember(eventId)`)
would have stopped the *document* read while this endpoint still handed over the image. So when
`events/{eventId}.access.mode == 'invite'`, this endpoint requires event membership on every branch.

Note what that did **not** need: no token scheme, no signed query parameter. The client already has an
authed-fetch → blob-URL path for the pool/self tiers (`frontend/src/lib/useAuthedImage.ts`), and
`frontend/src/lib/MediaImg.tsx` routes every image through it when the event is invite-only. An open
event's kiosk is untouched and still costs no auth round trip.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Header, Path, Query, Response
from fastapi.responses import JSONResponse, RedirectResponse

from schemas.common import Visibility
from shared import errors, fs, gcs, log
from shared.auth import Principal, verify_bearer

from .membership import is_invite_only

router = APIRouter(prefix="/v1/events/{eventId}", tags=["media"])

#: Matches VIDEO_URL_TTL_MINUTES in reels.py — long enough that a slideshow lingering on one
#: photo never expires mid-view, short enough that a scraped link is worthless the same afternoon.
RENDER_URL_TTL_MINUTES = 60

_VARIANT_FIELD = {"thumb": "thumbUri", "display": "displayUri"}


def _media(event_id: str, media_id: str) -> dict[str, Any]:
    doc = fs.media_ref(event_id, media_id).get().to_dict()
    if doc is None:
        raise errors.not_found("MEDIA_NOT_FOUND", "no such media")
    return doc


def _is_publicly_visible(doc: dict[str, Any]) -> bool:
    return doc.get("visibility") == Visibility.PUBLIC.value and doc.get("status") == "indexed"


def _is_subject(doc: dict[str, Any], principal: Principal) -> bool:
    return bool(principal.person_id) and principal.person_id in doc.get(
        "albumOf", []
    ) and doc.get("visibility") in (Visibility.POOL.value, Visibility.PUBLIC.value)


@router.get("/media/{mediaId}/render")
async def media_render(
    eventId: str = Path(min_length=1, max_length=128),
    mediaId: str = Path(min_length=1, max_length=64),
    variant: str = Query(default="display"),
    json: bool = Query(
        default=False,
        description="return {url, expiresInSec} instead of a 302 — the browser-fetch path",
    ),
    authorization: str | None = Header(default=None),
) -> Response:
    """302 to a short-lived signed URL for `variant` ('thumb' or 'display'). See module docstring.

    `?json=1` returns that same URL in a body instead of redirecting, and it exists because of a
    defect the 302 cannot avoid. A caller that needs a token here — every ring above `public`, and
    *everything* on an invite-only event — has to send `Authorization`, which makes the request
    non-simple, so the browser preflights it; the 302 then sends that preflighted request on to
    `storage.googleapis.com`, and a bucket CORS policy cannot allow the `Authorization` header
    (`deploy/buckets.sh` lists response headers, not request ones). The preflight fails, the fetch
    is blocked, and every private thumbnail renders as an empty tile — which is exactly what a
    guest's own matched album looked like.

    Handing back the URL splits the two hops: the *authorized* one talks only to this API (one
    preflight it can answer), and the *bytes* hop is then a plain `<img src>` — no header, no
    preflight, no CORS involvement at all. It is also faster, and cacheable by the browser."""
    field = _VARIANT_FIELD.get(variant)
    if field is None:
        raise errors.bad_request("BAD_VARIANT", "variant must be 'thumb' or 'display'")

    doc = _media(eventId, mediaId)

    # On an open event a public, fully-indexed photo needs no caller at all. On an invite-only one it
    # does: the whole point of shutting the door is that an eventId and a mediaId stop being enough.
    open_to_all = _is_publicly_visible(doc) and not is_invite_only(eventId)

    if not open_to_all:
        # Every other ring needs a real caller — the public branch above costs no auth round trip,
        # exactly like the reel video's.
        try:
            principal = verify_bearer(authorization)
        except Exception:  # noqa: BLE001 - an absent or bad token on a private photo is simply a 404
            raise errors.not_found("MEDIA_NOT_AVAILABLE", "this photo is not available") from None
        allowed = (
            principal.is_host_of(eventId)
            or principal.platform_admin
            or doc.get("uploaderUid") == principal.uid
            or _is_subject(doc, principal)
            # An invite-only event's members get exactly what the rules give them and nothing more:
            # the *same* public tier, gated on having come through `POST /join`. Membership never
            # widens a ring — a `pool` or `self` item still needs the subject or the uploader.
            or (_is_publicly_visible(doc) and principal.is_member_of(eventId))
        )
        if not allowed:
            raise errors.not_found("MEDIA_NOT_AVAILABLE", "this photo is not available")

    parsed = gcs.parse_gs_uri(str(doc.get(field) or ""))
    if parsed is None:
        raise errors.not_found("RENDER_NOT_READY", "this render has not been produced yet")

    bucket, path = parsed
    url = gcs.signed_get_url(
        bucket, path, ttl_minutes=RENDER_URL_TTL_MINUTES, response_type="image/webp"
    )
    log.info("media_render_served", event_id=eventId, media_id=mediaId, variant=variant)
    if json:
        return JSONResponse({"url": url, "expiresInSec": RENDER_URL_TTL_MINUTES * 60})
    return RedirectResponse(url, status_code=302)
