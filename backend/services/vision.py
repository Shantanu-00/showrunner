"""Cloud Vision SafeSearch — pass 1 of the Guardian, and the one gate no model can argue with.

Spec 03 §5.3 splits safety into two passes for a reason worth stating plainly: *category* and
*dignity* are different questions. "Is this pornography" is a classifier's job and the answer must
be non-negotiable; "is this photograph unkind to the person in it" is contextual judgment and needs
a language model with the event's stage context. Mixing them would put a probabilistic judge in
front of a hard gate, which is exactly the arrangement spec 04 §1 exists to forbid.

So this module answers only the first question, deterministically, with no prompt and no
temperature. Its output feeds `workers.safety.gate`, which treats it as a floor: the dignity rubric
can make a verdict *more* conservative than SafeSearch demanded, never less.

Same transient/permanent split as `services.gemini` (spec 03 §6), for the same reason — the worker's
retry decision needs the classification made where the exception still exists.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass

from google.api_core import exceptions as gexc
from google.cloud import vision

from shared import log

#: Vision returns an ordinal likelihood, not a score. Spec 03 §5.3's thresholds are all "≥ LIKELY",
#: so ordering is all this needs — but the names are kept for the stored `safeSearch` block, which
#: is what a host (or a judge) reads back off the media document.
_ORDER = {
    "UNKNOWN": 0,
    "VERY_UNLIKELY": 1,
    "UNLIKELY": 2,
    "POSSIBLE": 3,
    "LIKELY": 4,
    "VERY_LIKELY": 5,
}

#: The bar spec 03 §5.3 sets for every category it gates on.
LIKELY = _ORDER["LIKELY"]

_CATEGORIES = ("adult", "racy", "violence", "medical", "spoof")


class SafeSearchError(RuntimeError):
    """Base — carries the retry decision the caller acts on."""


class TransientSafeSearchError(SafeSearchError):
    """Quota, 5xx, deadline. Hand back to Cloud Tasks."""


class PermanentSafeSearchError(SafeSearchError):
    """Undecodable or unsupported image. Retrying buys nothing."""


@dataclass(frozen=True)
class SafeSearch:
    """One SafeSearch annotation, as likelihood *names* keyed by category."""

    adult: str = "UNKNOWN"
    racy: str = "UNKNOWN"
    violence: str = "UNKNOWN"
    medical: str = "UNKNOWN"
    spoof: str = "UNKNOWN"

    def rank(self, category: str) -> int:
        return _ORDER.get(str(getattr(self, category, "UNKNOWN")).upper(), 0)

    def at_least(self, category: str, bar: int = LIKELY) -> bool:
        return self.rank(category) >= bar

    def as_dict(self) -> dict[str, str]:
        """Stored verbatim on the media doc (`guardian.safeSearch`) — the gate's own evidence."""
        return {category: getattr(self, category) for category in _CATEGORIES}


@functools.lru_cache(maxsize=1)
def _client() -> vision.ImageAnnotatorClient:
    return vision.ImageAnnotatorClient()


def _name(value: object) -> str:
    """Proto enum → its name. `Likelihood` values arrive as enums, ints or already-strings."""
    if isinstance(value, str):
        return value.upper()
    name = getattr(value, "name", None)
    if isinstance(name, str):
        return name.upper()
    try:
        return vision.Likelihood(int(value)).name  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "UNKNOWN"


def safe_search(image_bytes: bytes) -> SafeSearch:
    """Annotate one image. Raises the transient/permanent split above, never a bare exception."""
    try:
        response = _client().safe_search_detection(image=vision.Image(content=image_bytes))
    except (gexc.ResourceExhausted, gexc.ServiceUnavailable, gexc.DeadlineExceeded,
            gexc.InternalServerError, gexc.TooManyRequests) as exc:
        raise TransientSafeSearchError(f"vision transient: {exc}") from exc
    except gexc.InvalidArgument as exc:
        raise PermanentSafeSearchError(f"vision rejected the image: {exc}") from exc
    except gexc.GoogleAPICallError as exc:
        # Anything else (403 on a missing role, 400 shapes we have not seen) is not fixed by a
        # retry, and a retry storm on a misconfiguration is worse than one clear alert.
        raise PermanentSafeSearchError(f"vision call failed: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 - network/transport oddities are the queue's problem
        raise TransientSafeSearchError(f"{type(exc).__name__}: {exc}") from exc

    # The API reports per-image failures in-band rather than by raising: an image it could not
    # process comes back with an empty annotation and an `error`. Treated as permanent, because
    # the bytes will not decode any better on attempt four.
    if response.error and response.error.message:
        raise PermanentSafeSearchError(f"vision image error: {response.error.message}")

    annotation = response.safe_search_annotation
    result = SafeSearch(
        adult=_name(annotation.adult),
        racy=_name(annotation.racy),
        violence=_name(annotation.violence),
        medical=_name(annotation.medical),
        spoof=_name(annotation.spoof),
    )
    log.debug("safesearch", **result.as_dict())
    return result
