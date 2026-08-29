"""`recompute_visibility` — the only writer of `media.visibility` in the entire system.

This is spec 04 §1's core principle in one file: **judgment by agents, enforcement by policy.**
The Curator writes an aesthetic score, the Guardian writes a verdict, a guest writes a consent
ring — all opinions, all inputs. This function is the only thing that turns those inputs into
exposure, it is deterministic, it runs in a transaction, and Firestore security rules serve
strictly what it decided. No language model can widen exposure by being wrong, and no race can
leak a photo, because there is exactly one writer and it always reads the whole document first.

Spec 04 §6 acceptance is a grep: exactly one writer of `visibility`. Keep it that way — every
other module calls this, and nothing else assigns that field.

Called by: every perception worker on stage completion, consent changes, subject vetoes, host
review decisions, deletions. Missing inputs are not an error: a half-processed item is `pool`
(private but shared with the people in it), never `public`. Every public-surface query filters
`status=='indexed'` as well, so `pool`-then-`public` is a state no viewer ever races through.
"""

from __future__ import annotations

from typing import Any, Callable

from google.cloud import firestore

from schemas.common import ConsentRing, GuardianVerdict, Visibility

from . import fs, log
from .settings import settings

#: Given the document as it will be *after* the caller's updates, return any further updates to
#: commit in the same transaction. Used for `status='indexed'` (see `shared.pipeline`).
Derive = Callable[[dict[str, Any]], dict[str, Any]]

#: A write to some *other* document that must land or not land with this one. The only user is the
#: coverage ledger (`shared.coverage`), and the reason it is a hook rather than a second call is
#: spec 05's requirement that the counters be exact: a coverage bump committed outside this
#: transaction could survive a rolled-back index, and one that ran before it could be lost by a
#: retry. Receives the transaction and the post-update document; may write, must not read after the
#: transaction has started writing, and must never raise.
SideEffect = Callable[[firestore.Transaction, dict[str, Any]], None]


def public_floor(event: dict[str, Any]) -> float:
    """The aesthetic bar for Ring 2 to actually reach a public surface (spec 04 §2).

    One field, read the same way for every event. There used to be a second path here — a
    `demoConfig.publicFloor` override honoured only when `class == 'protected_demo'` — and removing
    it (S14) is a trust decision rather than a tidy-up, so it is worth stating why.

    The demo event still runs at a floor of 0.0, because a judge's test photo of their desk should
    reach the kiosk `just_in` strip instead of reading as breakage (spec 09 §5). It gets there by
    setting **this** field, the ordinary one any host can set, in `scripts/seed_judge_event.py`. The
    class-conditional branch bought nothing that the plain field did not already provide, and what it
    cost was the only piece of exposure logic in the system whose condition was "is this event being
    looked at by a judge." The rule it failed, now binding on every later session (HANDOFF §9): *a
    demo convenience is honest if it is a configuration value a real host could also set; it is a
    thumb on the scale if it is a code branch keyed on whose event it is.*

    So there is deliberately nothing to special-case in this function. Note also what a floor of 0.0
    does not do: consent and the Guardian gates in `decide` below still apply in full, and the
    aesthetic score remains a *ranking* term on every surface that ranks.
    """
    floor = event.get("publicFloor")
    return float(floor) if floor is not None else settings().default_public_floor


def decide(media: dict[str, Any], event: dict[str, Any]) -> Visibility:
    """Pure function, no I/O — spec 04 §2 verbatim, in evaluation order.

    The order matters: the forced cases come first so that no later condition can rescue them.
    """
    guardian = media.get("guardian") or {}
    verdict = guardian.get("hostDecision") or guardian.get("verdict")

    # Forced to the uploader alone, consent irrelevant. `blocked` is the SafeSearch hard gate;
    # it stays visible to the host moderation area through an admin query path, not through here.
    if verdict == GuardianVerdict.BLOCKED.value:
        return Visibility.SELF

    if media.get("deleted") or media.get("duplicateOf"):
        return Visibility.SELF
    if int((media.get("consent") or {}).get("ring", ConsentRing.EVENT_POOL.value)) == (
        ConsentRing.SELF_ONLY.value
    ):
        return Visibility.SELF

    curator = media.get("curator") or {}
    aesthetic = float(curator.get("aestheticScore") or 0.0)

    if (
        int((media.get("consent") or {}).get("ring", ConsentRing.EVENT_POOL.value))
        == ConsentRing.PUBLIC.value
        and verdict == GuardianVerdict.PUBLIC_OK.value
        and aesthetic >= public_floor(event)
        and not (media.get("subjectVetoes") or [])
        # PANIC: freeze public (spec 08 §5). Enforced here because "only this function writes
        # visibility" is the invariant that makes the panic button trustworthy — a freeze applied
        # anywhere else would be a second writer, and therefore bypassable.
        and not event.get("publicFrozen")
    ):
        return Visibility.PUBLIC

    return Visibility.POOL


@firestore.transactional
def _apply(
    transaction: firestore.Transaction,
    ref: firestore.DocumentReference,
    event: dict[str, Any],
    extra: dict[str, Any] | None,
    derive: Derive | None,
    side_effect: SideEffect | None,
) -> tuple[str, str | None] | None:
    """Read the document, decide, write — atomically, so concurrent workers agree.

    Returns `(resolved, previous)`. The previous value is what lets the caller notice a *demotion*
    away from `public`, which is the only trigger for spec 06 §7's reel interlock.
    """
    snap = ref.get(transaction=transaction)
    if not snap.exists:
        return None
    media = snap.to_dict() or {}
    previous = media.get("visibility")

    updates: dict[str, Any] = dict(extra or {})
    # `extra` is the caller's own stage result. Applying it in this transaction is what makes
    # "the visibility field is always consistent with the latest verdicts" (spec 03 §3) true:
    # the verdict and the exposure it implies land in the same atomic write, so there is no
    # instant where the document says public_ok and visibility still says pool.
    merged = _merge(media, updates)

    if derive is not None:
        # Other derived fields (`status='indexed'`) computed from the same post-update snapshot.
        # They ride along rather than taking a second transaction because they are read together:
        # every public-surface query filters on `status` *and* `visibility`, so a gap between the
        # two writes is a window where a photo is public but unqueryable, or worse, the reverse.
        derived = derive(merged)
        if derived:
            updates.update(derived)
            merged = _merge(merged, derived)

    resolved = decide(merged, event)
    updates["visibility"] = resolved.value
    merged["visibility"] = resolved.value

    if side_effect is not None:
        # Before the media write, so a hook that needs a read of its own still has one available:
        # Firestore requires every read in a transaction to precede every write.
        side_effect(transaction, merged)

    transaction.update(ref, updates)
    return resolved.value, previous


def _merge(media: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    """Apply dotted-path updates onto a copy of the document, for the decision only.

    Firestore's `update` takes dotted paths (`guardian.verdict`) that the plain document does not
    have as keys, so a decision made against the pre-update document would miss the very verdict
    that triggered the recompute.
    """
    merged: dict[str, Any] = {k: v for k, v in media.items()}
    for path, value in updates.items():
        if isinstance(value, firestore.Increment) or value is fs.SERVER_TIMESTAMP:
            continue
        target = merged
        parts = path.split(".")
        for part in parts[:-1]:
            nested = target.get(part)
            target[part] = dict(nested) if isinstance(nested, dict) else {}
            target = target[part]
        target[parts[-1]] = value
    return merged


def recompute_visibility(
    event_id: str,
    media_id: str,
    *,
    event: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
    derive: Derive | None = None,
    side_effect: SideEffect | None = None,
) -> str | None:
    """Recompute and store `visibility`, optionally committing `extra` in the same transaction.

    Returns the resolved value, or None if the media document is gone.
    """
    resolved_event = event if event is not None else (fs.get_event(event_id) or {})
    outcome = _apply(
        fs.db().transaction(),
        fs.media_ref(event_id, media_id),
        resolved_event,
        extra,
        derive,
        side_effect,
    )
    if outcome is None:
        log.warn("visibility_media_missing", event_id=event_id, media_id=media_id)
        return None

    resolved, previous = outcome
    if previous == Visibility.PUBLIC.value and resolved != Visibility.PUBLIC.value:
        # Spec 06 §7's consent interlock. Only on the demotion, and only after the commit — see
        # `shared/reels.py` for why it is not inside the transaction and why `shared` owns it rather
        # than `directors/reel`. Never raises, so a reel that will not come down cannot stop a
        # photograph from going private.
        from . import reels

        reels.retract_containing(event_id, media_id)
    return resolved
