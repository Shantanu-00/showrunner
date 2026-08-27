"""Model Armor — the guardrail on every *text* surface that enters a prompt.

Three facts shape this file, all of them measured rather than assumed (B1-S1's `armor` probe,
friction log 2026-08-24 / 08-27):

1. **Text only.** Model Armor's image screening is Preview and one-image-per-request, which is the
   wrong shape for a photo pipeline — photos are gated by Vision SafeSearch plus the Guardian
   instead (spec 03 §5.3). What comes through here is host- and guest-*authored text*: the pasted
   itinerary (spec 08 §3), bounty briefs, captions.
2. **`us`/`eu` multi-region only, on its own endpoint host.** `modelarmor.{location}.rep.googleapis.com`
   — the location is read out of the configured template's resource name rather than configured
   twice, because two sources of truth for one value is how you get a 404 that looks like a
   permissions error.
3. **REST, not a client library.** No Model Armor client is pinned in `requirements.txt`; the probe
   proved the REST shape and this is the same call. `x-goog-user-project` is required or the request
   is billed to nowhere and rejected.

The API surface here is deliberately two-layered. `sanitize()` is the raw question. `guard()` is the
policy: it decides what a match *means*, writes the `ops/` record the Flight Deck renders as a
deflected chip (spec 10), and raises. Callers that must not proceed on a match call `guard()`;
nothing calls `sanitize()` and then decides for itself, because that is how a check becomes advisory.

**Fail-open is deliberate, and bounded.** If Model Armor itself is unreachable, text is allowed
through with a warning-level `ops/` alert. Armor is defence in depth: our own enforcement — Firestore
rules, `recompute_visibility`, structured output schemas — is what actually protects the system, and
an outage in an advisory service must not take down a host's onboarding wizard. A configuration
mistake (no template) is logged once and treated the same way.
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass, field
from typing import Any

import google.auth
import google.auth.transport.requests
from google.auth.transport.requests import AuthorizedSession

from shared import fs, log
from shared.settings import settings

_TEMPLATE_RE = re.compile(r"^projects/([^/]+)/locations/([^/]+)/templates/([^/]+)$")

#: Cheap upper bound on what we will ever ask Model Armor to look at. A pasted itinerary is a few
#: kilobytes; anything past this is either a mistake or an attempt to make the check expensive.
MAX_TEXT_BYTES = 32_000


class ArmorBlocked(RuntimeError):
    """The text matched a filter and the caller must not use it.

    Carries the matched filter names so the surface can tell the host *which* guardrail fired —
    "this looked like a prompt injection" is actionable, "rejected" is not.
    """

    def __init__(self, surface: str, filters: list[str]) -> None:
        super().__init__(f"model armor blocked {surface}: {', '.join(filters) or 'unspecified'}")
        self.surface = surface
        self.filters = filters


@dataclass(frozen=True)
class ArmorVerdict:
    matched: bool
    filters: list[str] = field(default_factory=list)
    #: False when no template is configured or the service was unreachable — i.e. the text was
    #: *not actually checked*. Recorded so a clean verdict can never be confused with an unchecked
    #: one, on the Flight Deck or in a log.
    checked: bool = True
    error: str | None = None


@functools.lru_cache(maxsize=1)
def _template() -> tuple[str, str] | None:
    """(resource name, location) from `MODEL_ARMOR_TEMPLATE`, or None if unconfigured."""
    raw = settings().model_armor_template
    if not raw:
        log.warn("armor_not_configured", detail="MODEL_ARMOR_TEMPLATE is empty — text is unchecked")
        return None
    match = _TEMPLATE_RE.match(raw.strip())
    if not match:
        log.error("armor_template_malformed", template=raw[:120])
        return None
    return raw.strip(), match.group(2)


@functools.lru_cache(maxsize=1)
def _session() -> AuthorizedSession:
    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    session = AuthorizedSession(credentials)
    # Model Armor bills the *quota* project, which ADC does not imply on its own.
    session.headers.update({"x-goog-user-project": settings().project})
    return session


def _matched_filters(payload: dict[str, Any]) -> tuple[bool, list[str]]:
    """Pull `(matched, filter names)` out of a `sanitizeUserPrompt` response.

    The response nests one level deeper than the docs suggest and the RAI family reports per-type
    results inside its own map, so this walks the tree rather than indexing into it — a shape change
    then costs a missing label, not an exception on the host's happy path.
    """
    sanitization = payload.get("sanitizationResult") or {}
    matched = sanitization.get("filterMatchState") == "MATCH_FOUND"
    names: list[str] = []
    for family, detail in (sanitization.get("filterResults") or {}).items():
        if not isinstance(detail, dict):
            continue
        for inner in detail.values():
            if not isinstance(inner, dict) or inner.get("matchState") != "MATCH_FOUND":
                continue
            hits = [
                key
                for key, result in (inner.get("raiFilterTypeResults") or {}).items()
                if isinstance(result, dict) and result.get("matchState") == "MATCH_FOUND"
            ]
            names.append(f"{family}({','.join(sorted(hits))})" if hits else str(family))
    return matched, sorted(set(names))


def sanitize(text: str, *, surface: str) -> ArmorVerdict:
    """Ask Model Armor about one piece of text. Never raises — see the module docstring."""
    if not text or not text.strip():
        return ArmorVerdict(matched=False)

    configured = _template()
    if configured is None:
        return ArmorVerdict(matched=False, checked=False, error="no template configured")

    name, location = configured
    body = text if len(text.encode("utf-8")) <= MAX_TEXT_BYTES else text[: MAX_TEXT_BYTES // 4]

    try:
        response = _session().post(
            f"https://modelarmor.{location}.rep.googleapis.com/v1/{name}:sanitizeUserPrompt",
            json={"userPromptData": {"text": body}},
            timeout=20,
        )
    except Exception as exc:  # noqa: BLE001 - advisory service: log, do not take the caller down
        log.warn("armor_unreachable", surface=surface, err=str(exc)[:200])
        return ArmorVerdict(matched=False, checked=False, error=str(exc)[:200])

    if response.status_code != 200:
        log.warn(
            "armor_call_failed",
            surface=surface,
            status=response.status_code,
            detail=response.text[:200],
        )
        return ArmorVerdict(
            matched=False, checked=False, error=f"http {response.status_code}"
        )

    matched, filters = _matched_filters(response.json())
    log.info(
        "armor_checked",
        surface=surface,
        matched=matched,
        filters=",".join(filters) or None,
    )
    return ArmorVerdict(matched=matched, filters=filters)


def guard(text: str, *, surface: str, event_id: str | None = None) -> str:
    """Check `text` and raise `ArmorBlocked` on a match. Returns the text unchanged otherwise.

    The `ops/` record on a block is the point as much as the block is: spec 10's Flight Deck renders
    a deflected red chip from it, and that beat is only worth filming because it is a real
    interception written by the code path that actually refused the input.
    """
    verdict = sanitize(text, surface=surface)
    if not verdict.matched:
        return text

    log.warn("armor_blocked", surface=surface, event_id=event_id, filters=",".join(verdict.filters))
    if event_id:
        fs.ops_alert(
            event_id,
            "armor_blocked",
            f"Model Armor rejected {surface}: {', '.join(verdict.filters) or 'policy match'}",
            severity="warning",
            surface=surface,
            filters=verdict.filters,
        )
    raise ArmorBlocked(surface, verdict.filters)
