"""Cloud Storage: object paths, signed URLs, resumable sessions.

Two things here are load-bearing for the trust architecture:

1. **Bytes never touch our servers** (spec 01 §1). The API hands out a V4 signed PUT URL with
   both `Content-Type` and `Content-Length` in the *signed headers*, so GCS itself rejects a PUT
   whose body length differs from what was declared. Size is enforced, not trusted.
2. **No key files, ever** (spec 09 §4). Signing normally needs a private key; instead we sign
   through the IAM `signBlob` API using the ambient service account. Works identically on Cloud
   Run (attached SA) and locally (ADC impersonating the same SA), with nothing on disk.
"""

from __future__ import annotations

import datetime as dt
import functools
from typing import Any

import google.auth
import google.auth.transport.requests
from google.cloud import storage

from . import log
from .settings import EXT_BY_CONTENT_TYPE, SIGNED_URL_TTL_MINUTES, settings


@functools.lru_cache(maxsize=1)
def client() -> storage.Client:
    return storage.Client(project=settings().project)


# ---------------------------------------------------------------- object paths (spec 01 §4)


def original_path(event_id: str, media_id: str, content_type: str) -> str:
    ext = EXT_BY_CONTENT_TYPE.get(content_type, "bin")
    return f"events/{event_id}/media/{media_id}/original.{ext}"


def derived_path(event_id: str, media_id: str, name: str) -> str:
    return f"events/{event_id}/media/{media_id}/{name}"


def quarantine_path(event_id: str, media_id: str, name: str) -> str:
    return f"quarantine/events/{event_id}/media/{media_id}/{name}"


def gs_uri(bucket: str, path: str) -> str:
    return f"gs://{bucket}/{path}"


def parse_gs_uri(uri: str) -> tuple[str, str] | None:
    """`gs://bucket/path` → (bucket, path), else None.

    Media docs store render locations as URIs, so every downstream worker starts by splitting one.
    Returning None instead of raising lets the caller classify a malformed URI as permanent — no
    number of retries will make it parse.
    """
    if not uri or not uri.startswith("gs://"):
        return None
    bucket, _, path = uri[len("gs://") :].partition("/")
    return (bucket, path) if bucket and path else None


def parse_object_path(name: str) -> tuple[str, str] | None:
    """`events/{eventId}/media/{mediaId}/original.ext` → (eventId, mediaId), else None.

    Strays (anything else landing in the raw bucket) are logged and acked, never retried.
    """
    parts = name.split("/")
    if len(parts) != 5 or parts[0] != "events" or parts[2] != "media":
        return None
    if not parts[4].startswith("original."):
        return None
    return parts[1], parts[3]


# ---------------------------------------------------------------- signing


def _signing_credentials() -> tuple[Any, str]:
    """Fresh ADC credentials plus the SA email to sign as.

    On Cloud Run the ambient credentials *are* the service account, so signing as
    `SIGNER_SA_EMAIL` is a self-signBlob call. Locally, ADC is a user account, which cannot
    sign — it needs roles/iam.serviceAccountTokenCreator on that SA (granted in deploy/sa.sh).
    """
    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    creds.refresh(google.auth.transport.requests.Request())

    signer = settings().signer_sa_email or getattr(creds, "service_account_email", "") or ""
    if not signer or signer == "default":
        raise RuntimeError(
            "cannot determine a signing service account — set SIGNER_SA_EMAIL "
            "(see deploy/sa.sh) so signed URLs can be minted via IAM signBlob"
        )
    return creds, signer


def signed_put_url(
    bucket: str,
    path: str,
    *,
    content_type: str,
    content_length: int,
    ttl_minutes: int = SIGNED_URL_TTL_MINUTES,
) -> tuple[str, dt.datetime]:
    """V4 signed PUT with content-type AND content-length pinned into the signature."""
    creds, signer = _signing_credentials()
    blob = client().bucket(bucket).blob(path)
    expires_at = dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=ttl_minutes)
    url = blob.generate_signed_url(
        version="v4",
        expiration=expires_at,
        method="PUT",
        content_type=content_type,
        # Signed header: GCS compares it to the actual request and 403s on a mismatch, so a
        # URL issued for 4 MB cannot be used to stream 5 GB.
        headers={"content-length": str(content_length)},
        service_account_email=signer,
        access_token=creds.token,
    )
    return url, expires_at


def signed_get_url(
    bucket: str,
    path: str,
    *,
    ttl_minutes: int = 60,
    response_type: str | None = None,
) -> str:
    """V4 signed GET — short-lived read access to one private object.

    Every bucket in this project has `--public-access-prevention` (deploy/buckets.sh), which is the
    right default and means a published reel still has to be *served* somehow. The answer is not to
    open the curated bucket: a public object stays fetchable by URL after spec 06 §7 unpublishes the
    reel, which would make the consent interlock a UI behaviour rather than an enforced one. Instead
    `api` re-checks the reel's `visibility` on every request and 302s here (`api/reels.py`), so the
    grant is a minute long and revocation is immediate.

    Same keyless signing path as `signed_put_url` — IAM signBlob as `SIGNER_SA_EMAIL`, no key file.
    """
    creds, signer = _signing_credentials()
    blob = client().bucket(bucket).blob(path)
    return blob.generate_signed_url(
        version="v4",
        expiration=dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=ttl_minutes),
        method="GET",
        response_type=response_type,
        service_account_email=signer,
        access_token=creds.token,
    )


def resumable_session(
    bucket: str,
    path: str,
    *,
    content_type: str,
    content_length: int,
    origin: str | None = None,
) -> str:
    """Initiate a GCS resumable upload session for videos (spec 01 §2.2).

    The returned URI is a bearer token valid for a week — callers must never log it.
    `size` pins `x-upload-content-length`, so GCS enforces the declared length here too.
    """
    blob = client().bucket(bucket).blob(path)
    return blob.create_resumable_upload_session(
        content_type=content_type,
        size=content_length,
        origin=origin,
    )


# ---------------------------------------------------------------- object operations


def get_blob(bucket: str, path: str) -> storage.Blob | None:
    blob = client().bucket(bucket).get_blob(path)
    return blob


def download_bytes(bucket: str, path: str) -> bytes:
    return client().bucket(bucket).blob(path).download_as_bytes()


def upload_bytes(
    bucket: str,
    path: str,
    data: bytes,
    *,
    content_type: str,
    cache_control: str | None = None,
    if_generation_match: int | None = None,
) -> str:
    blob = client().bucket(bucket).blob(path)
    if cache_control:
        blob.cache_control = cache_control
    blob.upload_from_string(
        data, content_type=content_type, if_generation_match=if_generation_match
    )
    return gs_uri(bucket, path)


def copy_object(src_bucket: str, src_path: str, dst_bucket: str, dst_path: str) -> None:
    source = client().bucket(src_bucket)
    blob = source.blob(src_path)
    source.copy_blob(blob, client().bucket(dst_bucket), dst_path)


def delete_object(bucket: str, path: str) -> None:
    """Best-effort delete — a missing object is the desired end state either way."""
    try:
        client().bucket(bucket).blob(path).delete()
    except Exception as exc:  # noqa: BLE001
        log.warn("gcs_delete_failed", bucket=bucket, path=path, err=str(exc))
