"""Authenticated service-to-service calls between our own private Cloud Run services.

`api` makes exactly two synchronous calls into other services in the fleet, and both are
deliberate exceptions to "everything async goes through Cloud Tasks":

- **`worker-face`'s `/embed`** for the selfie enrollment/re-claim path (spec 02 §3). InsightFace
  lives in exactly one container (spec 09 §1), so anything needing an embedding outside the Cloud
  Tasks fan-out asks that container rather than loading a 326 MB model a second time.
- **`publisher`'s `/recompute`** from the director tick (spec 04 §4's fallback recompute trigger).
  Synchronous because the tick wants to know whether the wall was refreshed in order to report it,
  and because a queue hop would add a throttle in front of a once-per-two-minutes call.

The ID token comes from impersonating `SIGNER_SA_EMAIL` (`sa-api`), the same identity and the
same IAM role (`roles/iam.serviceAccountTokenCreator` on itself, granted in deploy/sa.sh) that
`shared/gcs.py` already uses for signed URLs — one mechanism, three uses, no key file any time.
"""

from __future__ import annotations

import functools

import google.auth
import google.auth.impersonated_credentials
import google.auth.transport.requests
import requests

from . import log
from .settings import settings


class FaceServiceError(Exception):
    """Raised on anything that means `worker-face` did not answer — caller decides retry policy."""


class PublisherError(Exception):
    """Raised when the publisher could not be reached or refused the nudge. Never fatal."""


@functools.lru_cache(maxsize=8)
def _id_token_credentials(audience: str) -> google.auth.impersonated_credentials.IDTokenCredentials:
    source, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    signer = settings().signer_sa_email
    if not signer:
        raise FaceServiceError("SIGNER_SA_EMAIL not configured — cannot mint an ID token")
    # IDTokenCredentials wants an already-impersonated `target_credentials`, not the source
    # directly — the self-impersonation step is what actually calls IAM as `signer`.
    impersonated = google.auth.impersonated_credentials.Credentials(
        source_credentials=source,
        target_principal=signer,
        target_scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )
    return google.auth.impersonated_credentials.IDTokenCredentials(
        impersonated, target_audience=audience, include_email=True
    )


def _bearer_for(audience: str) -> str:
    creds = _id_token_credentials(audience)
    creds.refresh(google.auth.transport.requests.Request())
    return creds.token


def embed_selfie(image_b64: str, *, max_faces: int = 1, timeout_s: float = 20.0) -> dict:
    """POST `{WORKER_FACE_URL}/embed` and return the parsed JSON body (schemas.faces.EmbedResponse).

    Raises `FaceServiceError` on anything that stops enrollment cold — no configured URL, a
    non-2xx response, or a network failure. The caller (api/identity.py) turns that into a 503;
    a selfie call has no Cloud Tasks retry loop underneath it to lean on.
    """
    url = settings().face_url
    if not url:
        raise FaceServiceError("WORKER_FACE_URL is not configured")
    try:
        resp = requests.post(
            f"{url}/embed",
            json={"image": image_b64, "maxFaces": max_faces},
            headers={"Authorization": f"Bearer {_bearer_for(url)}"},
            timeout=timeout_s,
        )
    except requests.RequestException as exc:
        log.warn("face_embed_call_failed", err=str(exc))
        raise FaceServiceError(f"could not reach worker-face: {exc}") from exc
    if resp.status_code >= 400:
        log.warn("face_embed_bad_status", status=resp.status_code, body=resp.text[:300])
        raise FaceServiceError(f"worker-face returned {resp.status_code}: {resp.text[:200]}")
    return resp.json()


def nudge_publisher(event_id: str, *, reason: str, timeout_s: float = 25.0) -> dict:
    """Ask the publisher to rebuild one event's kiosk playlist now (spec 04 §4's fallback trigger).

    The publisher normally needs no nudging: it holds a Firestore listener per leased event and
    rebuilds on push. This exists for the two cases where that listener is not running — a
    scaled-to-zero deployment during the judging month, and an instance that lost its lease and has
    not yet re-acquired it — so a Cloud Scheduler tick is always sufficient to refresh the wall.

    Raises `PublisherError`, which the tick logs and moves past: the wall going one tick stale is not
    a reason to fail a tick that may also have issued a bounty.
    """
    url = settings().publisher_url
    if not url:
        raise PublisherError("PUBLISHER_URL is not configured")
    try:
        resp = requests.post(
            f"{url}/recompute",
            json={"eventId": event_id, "reason": reason},
            headers={"Authorization": f"Bearer {_bearer_for(url)}"},
            timeout=timeout_s,
        )
    except requests.RequestException as exc:
        raise PublisherError(f"could not reach publisher: {exc}") from exc
    if resp.status_code >= 400:
        raise PublisherError(f"publisher returned {resp.status_code}: {resp.text[:200]}")
    try:
        return resp.json()
    except ValueError as exc:
        raise PublisherError("publisher returned a non-JSON body") from exc
