"""Every read and write the reel pipeline makes on `events/{eventId}/reels/{reelId}`.

Kept apart from the steps that decide things, for the same reason `publisher/store.py` is: the queries
a surface depends on should be visible in one place, and the reel document is read by the publisher
(premiere selection), the kiosk (`ReelSlot`), the host console and the API's video redirect. Four
clients means the field names here are a contract, not an implementation detail.

Two writes carry real weight:

**`publish` re-validates the manifest inside a transaction.** Spec 06 §3 step 6: a render that *started*
eligible can still be refused publication. Every asset in `assetManifest` is re-read and re-checked
against its current `visibility` before the reel's own `visibility` is set, so a guest who retracted
consent while the ffmpeg job was running does not get published into a reel three minutes later. This is
the same discipline as `recompute_visibility` — the reel's exposure is *derived* from the exposure of
its constituents, never asserted alongside it.

**`retract` is idempotent and one-way.** Spec 06 §7's interlock fires from
`shared/reels.py`, which fires from `recompute_visibility` the moment any constituent stops being
public. It flips `status` to `unpublished` and clears `visibility`, and it never un-does that: a reel
that lost an asset needs a new *version*, not a resurrection of the old file (spec 06 §4's "never inject
mid-render", applied to the publish side).
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from schemas.common import MediaStatus, Visibility
from schemas.reel import ReelDoc, ReelPersona, ReelStatus
from shared import fs, log
from shared.settings import REEL_STALE_MINUTES
from shared.ulid import new_ulid

#: Statuses that mean "this commission is still in flight", for the per-persona serialisation check.
IN_FLIGHT = (
    ReelStatus.DIRECTING.value,
    ReelStatus.COMPOSING.value,
    ReelStatus.RENDERING.value,
)


def ref(event_id: str, reel_id: str) -> firestore.DocumentReference:
    return fs.reels_col(event_id).document(reel_id)


def get(event_id: str, reel_id: str) -> dict[str, Any] | None:
    snap = ref(event_id, reel_id).get()
    return snap.to_dict() if snap.exists else None


def create(
    event_id: str,
    *,
    persona: ReelPersona,
    stage_id: str | None = None,
    person_id: str | None = None,
    audience_ring: int = 2,
    commissioned_by: str = "director",
    reason: str = "",
) -> str:
    """Write the commission. Status `directing`, no assets, no file — nothing is claimed yet."""
    reel_id = new_ulid()
    doc = ReelDoc(
        reelId=reel_id,
        persona=persona,
        stageId=stage_id,
        personId=person_id,
        audienceRing=audience_ring,
        commissionedBy=commissioned_by,
        commissionReason=reason[:300],
    )
    payload = fs.to_firestore(doc.model_dump())
    payload["createdAt"] = fs.SERVER_TIMESTAMP
    payload["updatedAt"] = fs.SERVER_TIMESTAMP
    ref(event_id, reel_id).set(payload)
    log.info(
        "reel_commissioned",
        event_id=event_id,
        reel_id=reel_id,
        persona=persona.value,
        by=commissioned_by,
    )
    return reel_id


def patch(event_id: str, reel_id: str, **fields: Any) -> None:
    """One update, always stamping `updatedAt` — the host console's "is anything happening" signal."""
    if not fields:
        return
    fields["updatedAt"] = fs.SERVER_TIMESTAMP
    ref(event_id, reel_id).set(fs.to_firestore(fields), merge=True)


def progress(event_id: str, reel_id: str, pct: int, *, status: ReelStatus | None = None) -> None:
    """Spec 06 §3 step 5's "job writes progress → reel doc (client shows 'rendering 60%')".

    A real percentage from the pipeline's own stage boundaries, not a timer: the client's bar is
    allowed to sit still, and a bar that moves while nothing happens is worse than one that waits.
    """
    fields: dict[str, Any] = {"progress": max(0, min(100, int(pct)))}
    if status is not None:
        fields["status"] = status.value
    patch(event_id, reel_id, **fields)


def fail(event_id: str, reel_id: str, reason: str, *, alert: bool = True) -> None:
    patch(
        event_id,
        reel_id,
        status=ReelStatus.FAILED.value,
        failureReason=reason[:500],
        visibility=None,
    )
    if alert:
        fs.ops_alert(
            event_id,
            "reel_failed",
            f"reel {reel_id} could not be produced: {reason[:200]}",
            severity="warning",
            reelId=reel_id,
        )


# ---------------------------------------------------------------- serialisation


def in_flight_of_persona(
    event_id: str, persona: ReelPersona, *, stage_id: str | None = None
) -> str | None:
    """The reelId of a commission of this persona that is still running, if there is one.

    Spec 06 §3's "commissions are serialized per persona (one active render each)". The spec reaches
    for the `renders` Cloud Tasks queue's `max-concurrent=2` for this; that dial is global and cannot
    express "per persona, per event", so the invariant lives here instead — a read inside the tick
    lease the Story Director already holds, which is strictly stronger than a queue setting.

    `REEL_STALE_MINUTES` is the crash backstop: the process holding a commission is a Cloud Run Job
    execution, and a job that dies leaves its document in `rendering` forever. After the timeout the
    next commission is allowed through and the stale one is marked failed by the caller.
    """
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=REEL_STALE_MINUTES)
    query = fs.reels_col(event_id).where(
        filter=FieldFilter("status", "in", list(IN_FLIGHT))
    )
    for snap in query.stream():
        doc = snap.to_dict() or {}
        if doc.get("persona") != persona.value:
            continue
        if stage_id is not None and doc.get("stageId") != stage_id:
            continue
        updated = doc.get("updatedAt") or doc.get("createdAt")
        if isinstance(updated, dt.datetime) and updated < cutoff:
            log.warn("reel_stale_in_flight", event_id=event_id, reel_id=snap.id)
            fail(event_id, snap.id, "abandoned: no progress within the stale window", alert=False)
            continue
        return snap.id
    return None


# ---------------------------------------------------------------- publish


@firestore.transactional
def _publish(
    transaction: firestore.Transaction,
    event_id: str,
    reel_ref: firestore.DocumentReference,
    manifest: list[str],
    audience_ring: int,
) -> tuple[bool, list[str]]:
    """Re-read every constituent, then set the reel's exposure. Atomic with the status flip."""
    snap = reel_ref.get(transaction=transaction)
    if not snap.exists:
        return False, ["reel document disappeared"]
    doc = snap.to_dict() or {}
    if doc.get("status") == ReelStatus.PUBLISHED.value:
        return True, []  # idempotent: a re-delivered publish is a no-op, not a second premiere

    required = Visibility.PUBLIC.value if audience_ring >= 2 else Visibility.POOL.value
    ineligible: list[str] = []
    for media_id in manifest:
        media_snap = fs.media_ref(event_id, media_id).get(transaction=transaction)
        if not media_snap.exists:
            ineligible.append(f"{media_id}: deleted")
            continue
        media = media_snap.to_dict() or {}
        visibility = media.get("visibility")
        if media.get("deleted"):
            ineligible.append(f"{media_id}: deleted")
        elif visibility == Visibility.SELF.value:
            ineligible.append(f"{media_id}: retracted to self")
        elif required == Visibility.PUBLIC.value and visibility != Visibility.PUBLIC.value:
            ineligible.append(f"{media_id}: no longer public ({visibility})")
        elif media.get("status") != MediaStatus.INDEXED.value:
            ineligible.append(f"{media_id}: no longer indexed")

    if ineligible:
        transaction.update(
            reel_ref,
            {
                "status": ReelStatus.UNPUBLISHED.value,
                "visibility": None,
                "failureReason": "constituents became ineligible before publication: "
                + "; ".join(ineligible[:6]),
                "updatedAt": fs.SERVER_TIMESTAMP,
            },
        )
        return False, ineligible

    transaction.update(
        reel_ref,
        {
            "status": ReelStatus.PUBLISHED.value,
            # Ring 2 → the same `public` vocabulary media uses, which is what `firestore.rules` and the
            # publisher's `reel_query` already filter on. A Ring-1 reel is deliberately left at `self`:
            # the private-reel read path belongs to the `main_character` persona, which spec 06's B4
            # descoping cut, and inventing a rule for it now would be a consent decision made by a
            # session that is not building the surface it protects.
            "visibility": Visibility.PUBLIC.value if audience_ring >= 2 else Visibility.SELF.value,
            "publishedAt": fs.SERVER_TIMESTAMP,
            "progress": 100,
            "updatedAt": fs.SERVER_TIMESTAMP,
        },
    )
    return True, []


def publish(event_id: str, reel_id: str, *, manifest: list[str], audience_ring: int) -> bool:
    """Spec 06 §3 step 6. Returns True when the reel went live; False when it was refused."""
    ok, ineligible = _publish(
        fs.db().transaction(), event_id, ref(event_id, reel_id), manifest, audience_ring
    )
    if ok:
        log.info("reel_published", event_id=event_id, reel_id=reel_id, assets=len(manifest))
    else:
        log.warn("reel_publish_refused", event_id=event_id, reel_id=reel_id, why=";".join(ineligible[:3]))
        fs.ops_alert(
            event_id,
            "reel_publish_refused",
            f"reel {reel_id} finished rendering but was refused publication: {'; '.join(ineligible[:4])}",
            severity="warning",
            reelId=reel_id,
        )
    return ok


def retract(event_id: str, reel_id: str, reason: str) -> None:
    """Spec 06 §7's consent interlock, applied to one reel. One-way and idempotent."""
    patch(
        event_id,
        reel_id,
        status=ReelStatus.UNPUBLISHED.value,
        visibility=None,
        failureReason=reason[:300],
    )
    log.warn("reel_retracted", event_id=event_id, reel_id=reel_id, reason=reason[:120])
