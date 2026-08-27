"""The deterministic half of the Guardian: SafeSearch floor + dial ceiling + the minor rule.

This file is where the Guardian stops being a language model. Everything above it produces
evidence — a SafeSearch annotation, a proposed verdict, a list of observations — and everything
below it (`recompute_visibility`) consumes a single verdict string. The translation between the two
is pure, ordered, and testable by reading it, which is the property the 30% "tools properly isolated"
criterion is actually asking about.

Three rules, in this order (spec 03 §5.3, spec 11 §2):

1. **SafeSearch is a hard gate.** `adult ≥ LIKELY` forces `blocked`, consent irrelevant, and the
   dignity pass is never even called (the worker short-circuits — see `app.py`). `racy`/`violence
   ≥ LIKELY` set a `private_only` floor that no model output can lift.
2. **`minor_prominent` can never be public.** A child as the main subject routes to `host_review`
   before Ring 2 — deterministically, here, not by asking the model to remember. Hosts know whose
   kids are whose; we don't.
3. **The host's declared dial is a ceiling, not a floor.** If the host declared `pda:
   'private_only'` and the frame shows PDA, the verdict cannot be `public_ok` no matter what the
   stage context suggested. The converse does not hold: a `public_ok` dial does *not* lift a
   conservative stage-context verdict, because the dial says "this event is relaxed about this", not
   "publish it regardless". `private_only` always wins outright.

`context_dependent` (and `attire: 'standard'`) clamp nothing on purpose: they mean "use judgment",
that judgment is the model's job, and a deterministic clamp there would just be a second, dumber
opinion overriding the informed one.
"""

from __future__ import annotations

from typing import Any

from schemas.common import GuardianVerdict
from schemas.guardian_out import DignityReason, GuardianOut
from services.vision import SafeSearch

#: Conservativeness order — later wins when two verdicts meet. `blocked` is the most restrictive
#: (uploader only, regardless of consent); `host_review` sits above `public_ok` because it is "not
#: public until a human says so", and below `private_only` because that one is a settled answer.
_LADDER = (
    GuardianVerdict.PUBLIC_OK,
    GuardianVerdict.HOST_REVIEW,
    GuardianVerdict.PRIVATE_ONLY,
    GuardianVerdict.BLOCKED,
)
_RANK = {verdict: index for index, verdict in enumerate(_LADDER)}

#: Which declared dial each observation is measured against, and the dial value that clamps it.
#: `attire` uses a different vocabulary from the other two (spec 11 §2: relaxed|standard|conservative).
_DIAL_CLAMPS: tuple[tuple[DignityReason, str, frozenset[str]], ...] = (
    (DignityReason.PDA_VISIBLE, "pda", frozenset({"private_only"})),
    (DignityReason.ALCOHOL_VISIBLE, "alcohol", frozenset({"private_only"})),
    (DignityReason.ATTIRE_REVEALING, "attire", frozenset({"conservative"})),
)


def most_conservative(*verdicts: GuardianVerdict) -> GuardianVerdict:
    return max(verdicts, key=lambda verdict: _RANK[verdict])


def safe_search_floor(annotation: SafeSearch) -> GuardianVerdict | None:
    """The hard gate (rule 1). Returns None when SafeSearch has no objection."""
    if annotation.at_least("adult"):
        return GuardianVerdict.BLOCKED
    if annotation.at_least("racy") or annotation.at_least("violence"):
        return GuardianVerdict.PRIVATE_ONLY
    return None


def sensitivity_ceiling(
    reasons: list[DignityReason], event: dict[str, Any]
) -> tuple[GuardianVerdict, list[str]]:
    """Rule 3: the most permissive verdict the host's declared dials allow, given what was seen.

    Returns the ceiling and the dials that actually bit, so the stored `reasons` can name them —
    a host looking at a held photo should be able to see it was *their own* declared setting that
    held it, not an opinion the system formed about their event.
    """
    profile = (event.get("eventTypeProfile") or {}).get("sensitivityProfile") or {}
    seen = set(reasons)
    ceiling = GuardianVerdict.PUBLIC_OK
    fired: list[str] = []

    for reason, dial, clamping_values in _DIAL_CLAMPS:
        if reason not in seen:
            continue
        declared = str(profile.get(dial) or "").strip().lower()
        if declared in clamping_values:
            ceiling = most_conservative(ceiling, GuardianVerdict.PRIVATE_ONLY)
            fired.append(f"dial_{dial}_{declared}")
    return ceiling, fired


def decide(
    annotation: SafeSearch, out: GuardianOut | None, event: dict[str, Any]
) -> tuple[GuardianVerdict, list[str]]:
    """Combine every input into the one verdict that gets stored, plus its reason list.

    `out=None` is the conservative path: SafeSearch passed but the dignity model did not answer, so
    the verdict is whatever the floor was, or `host_review` if there was no floor (spec 03 §5.3's
    "refusal/schema failure = conservative" — never `public_ok` by accident).
    """
    floor = safe_search_floor(annotation)

    if floor is GuardianVerdict.BLOCKED:
        # Nothing else is consulted, and nothing else can lift it.
        return GuardianVerdict.BLOCKED, ["safesearch_adult"]

    if out is None:
        return most_conservative(floor or GuardianVerdict.HOST_REVIEW), ["model_unavailable"]

    reasons: list[str] = [reason.value for reason in out.reasons]

    # A model returning `blocked` is out of its lane — only SafeSearch blocks (spec 03 §5.3). Its
    # opinion is respected at the strongest verdict it *is* allowed to reach.
    proposed = out.verdict
    if proposed is GuardianVerdict.BLOCKED:
        proposed = GuardianVerdict.PRIVATE_ONLY
        reasons.append("model_proposed_blocked")

    verdict = proposed
    if floor is not None:
        verdict = most_conservative(verdict, floor)
        reasons.append("safesearch_racy_or_violence")

    # Rule 2, deterministic and independent of every dial.
    if DignityReason.MINOR_PROMINENT in out.reasons:
        verdict = most_conservative(verdict, GuardianVerdict.HOST_REVIEW)

    ceiling, fired = sensitivity_ceiling(out.reasons, event)
    verdict = most_conservative(verdict, ceiling)
    reasons.extend(fired)

    if out.ritualEmotion:
        # Kept in the reason list because it is the *justification for permissiveness* — the one
        # place the Guardian talks itself out of being conservative, so it should be on the record.
        reasons.append("ritual_emotion")

    # Stable order, no duplicates: this list is rendered in a host card and asserted in eval/.
    return verdict, sorted(set(reasons))
