"""The reel side of the consent interlock (spec 06 §7), reachable from `shared` without importing a director.

Spec 06 §7: any constituent losing Ring-2 eligibility — a subject veto, a consent flip, a host pull, a
delete-my-data — takes the reel down immediately. That has to fire from wherever exposure is decided,
and exposure is decided in exactly one place (`shared/visibility.py`). So the hook lives here, in
`shared`, where `visibility.py` can call it without `shared` depending on `directors/` — a dependency
that would drag ADK and the whole reel pipeline into every worker's import path.

**It fires only on a public → non-public transition.** Running an `array_contains` query over `reels`
on every recompute would mean one extra query per stage completion per photograph, and the vast majority
of those recomputes are `pool → pool` during ordinary processing. A demotion away from `public` is rare
and is exactly the event the interlock exists for, so the check is both cheap and complete.

It runs *after* the transaction commits rather than inside it, deliberately. The unpublish is not an
exactness requirement — spec 06 §8 asks for ≤ 5 s, not atomicity — and a query cannot be issued after a
transaction has started writing anyway. What matters is that the media document is already non-public
when this runs, so a kiosk that reads the reel in the intervening milliseconds finds a reel whose
constituent is already gone from every public query.
"""

from __future__ import annotations

from google.cloud.firestore_v1.base_query import FieldFilter

from . import fs, log

#: Reel statuses the interlock acts on. A reel still rendering is left alone: its publish step
#: re-validates the whole manifest anyway (`directors/reel/store.py::publish`), so it will refuse
#: itself, and reaching into a running job's document would race with it.
LIVE_STATUSES = ("published",)


def retract_containing(event_id: str, media_id: str) -> list[str]:
    """Unpublish every live reel whose `assetManifest` contains `media_id`. Returns their ids.

    Never raises: this is called from the tail of `recompute_visibility`, and a failure to take a reel
    down must not roll back the retraction of the photograph itself — the photograph is the thing the
    guest asked to be private, and it already is.
    """
    retracted: list[str] = []
    try:
        query = (
            fs.reels_col(event_id)
            .where(filter=FieldFilter("assetManifest", "array_contains", media_id))
            .where(filter=FieldFilter("status", "in", list(LIVE_STATUSES)))
        )
        for snap in query.stream():
            snap.reference.set(
                {
                    "status": "unpublished",
                    "visibility": None,
                    "failureReason": f"constituent {media_id} is no longer eligible (spec 06 §7)",
                    "updatedAt": fs.SERVER_TIMESTAMP,
                },
                merge=True,
            )
            retracted.append(snap.id)
    except Exception as exc:  # noqa: BLE001 - the retraction that mattered already committed
        log.error("reel_interlock_failed", event_id=event_id, media_id=media_id, err=str(exc))
        return retracted

    if retracted:
        log.warn(
            "reel_interlock_fired",
            event_id=event_id,
            media_id=media_id,
            reels=",".join(retracted),
        )
        fs.ops_alert(
            event_id,
            "reel_unpublished",
            f"{len(retracted)} published reel(s) were taken down because {media_id} lost public "
            "eligibility. A replacement version has to be commissioned.",
            media_id=media_id,
            severity="warning",
            reelIds=retracted,
        )
    return retracted
