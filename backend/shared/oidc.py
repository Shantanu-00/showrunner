"""Verifying the Google-signed OIDC tokens our own infrastructure presents to `api`.

Every other service in the fleet is `--no-allow-unauthenticated`, so Cloud Run itself checks the
caller and the handler never sees an unauthorised request. `api` cannot work that way: it is the
one public surface (guests' phones hold Firebase ID tokens, not Google service-account tokens),
and Cloud Run's IAM check is per-service, not per-path. So `/internal/tick` — the endpoint Cloud
Scheduler calls, and the single most security-relevant unauthenticated path in the system, since
it is what makes the fleet act — has to verify its caller in the handler.

Two claims are checked, and both matter:

- **`email`** must be one of the service accounts we deployed for this purpose (`sa-scheduler`,
  `sa-tasks`). This is the real authorisation decision: Google will happily sign an ID token for
  any service account in any project, so "the signature is valid" means nothing on its own.
- **`aud`** must name *this* service. Cloud Scheduler and Cloud Tasks both mint their token with
  an audience we choose at job-creation time, and requiring it to match the host the request
  actually arrived on turns a token minted for some other endpoint into a rejection rather than a
  replay. The expected value comes from the request, not from configuration, so there is no
  second copy of the URL to drift out of sync (the same reasoning as `MODEL_ARMOR_TEMPLATE`
  carrying its own location).

`verify_oauth2_token` fetches Google's signing certificates over HTTPS per call. At two ticks a
minute that is irrelevant, and a stale-cache bug on an auth path would not be.
"""

from __future__ import annotations

import functools
from typing import Any
from urllib.parse import urlsplit

import google.auth.transport.requests
from google.oauth2 import id_token as google_id_token

from . import log


class InvalidServiceToken(Exception):
    """The bearer token is not a Google OIDC token we accept. Always a 401/403, never a retry."""


@functools.lru_cache(maxsize=1)
def _transport() -> google.auth.transport.requests.Request:
    return google.auth.transport.requests.Request()


def audience_host(value: str) -> str:
    """The host part of an audience/URL, lowercased. `''` when it is not a URL at all."""
    return (urlsplit(value).hostname or "").lower()


def verify(token: str, *, allowed_emails: set[str], expected_host: str) -> dict[str, Any]:
    """Return the verified claims, or raise `InvalidServiceToken`.

    `expected_host` is the host this request arrived on; an empty value skips the audience check,
    which only happens when a proxy has stripped the Host header (never on Cloud Run).
    """
    allowed = {e.strip().lower() for e in allowed_emails if e and e.strip()}
    if not allowed:
        # Fail closed. An empty allowlist means the deploy did not record the scheduler identity,
        # and treating that as "allow everyone" would turn a misconfiguration into an open door.
        raise InvalidServiceToken("no service-account allowlist is configured for this endpoint")

    try:
        # audience=None: the library's own check takes a single string, and we need to compare a
        # *host* (the job may be configured with a path or query on its audience).
        claims = google_id_token.verify_oauth2_token(token, _transport(), audience=None)
    except Exception as exc:  # noqa: BLE001 - any verification failure is one rejection
        raise InvalidServiceToken(f"token verification failed: {type(exc).__name__}") from exc

    email = str(claims.get("email") or "").lower()
    if not claims.get("email_verified") or email not in allowed:
        log.warn("internal_token_rejected", email=email or None, reason="email_not_allowed")
        raise InvalidServiceToken("this identity may not call internal endpoints")

    if expected_host:
        got = audience_host(str(claims.get("aud") or ""))
        if got != expected_host.lower():
            log.warn("internal_token_rejected", email=email, reason="audience_mismatch", aud=got)
            raise InvalidServiceToken("token audience does not name this service")

    return claims
