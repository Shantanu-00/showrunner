"""`dlq` service — the last stop for a finalize event that intake could not process.

Eventarc retries with backoff for 24 h; after 5 failed delivery attempts the underlying Pub/Sub
subscription dead-letters the message here (wired in `deploy/eventarc.sh`). This service exists
so that failure is *visible* rather than silent: the media doc lands in `quarantined` and the host
console gets a red badge, instead of a photo quietly never appearing.

It always acks (2xx). A DLQ that can itself dead-letter is not a DLQ.
"""

from __future__ import annotations

import base64
import json
from typing import Any

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from google.cloud import firestore

from schemas.common import MediaStatus
from shared import fs, gcs, log
from shared.settings import settings

log.configure("dlq")

app = FastAPI(title="Showrunner DLQ", version="0.1.0", docs_url=None, redoc_url=None)

#: Statuses a dead-lettered event must not clobber — the media got there by another delivery.
TERMINAL = (
    MediaStatus.INDEXED.value,
    MediaStatus.REJECTED.value,
    MediaStatus.QUARANTINED.value,
)


@app.get("/livez")
async def livez() -> dict[str, str]:
    return {"status": "ok", "service": "dlq", "environment": settings().environment}


@firestore.transactional
def _quarantine_doc(
    transaction: firestore.Transaction,
    ref: firestore.DocumentReference,
    reason: str,
) -> str:
    snap = ref.get(transaction=transaction)
    if not snap.exists:
        return "missing"
    status = (snap.to_dict() or {}).get("status")
    if status in TERMINAL:
        return "terminal"
    transaction.update(
        ref,
        {
            "status": MediaStatus.QUARANTINED.value,
            "quarantineReason": reason,
            "quarantinedAt": fs.SERVER_TIMESTAMP,
        },
    )
    return "quarantined"


@app.post("/")
async def on_dead_letter(request: Request) -> dict[str, Any]:
    try:
        envelope = await request.json()
    except Exception:  # noqa: BLE001
        log.error("dlq_unparseable_envelope")
        return {"ok": True, "skipped": "unparseable"}
    return await run_in_threadpool(process, envelope if isinstance(envelope, dict) else {})


def _object_ref(envelope: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    """Recover (bucket, objectPath) from a Pub/Sub push envelope.

    Two sources, because the shape depends on how far the message got: GCS notification
    attributes (`bucketId`/`objectId`) survive even when the payload does not.
    """
    message = envelope.get("message") if isinstance(envelope.get("message"), dict) else {}
    attributes = message.get("attributes") or {}
    payload: dict[str, Any] = {}
    raw = message.get("data")
    if isinstance(raw, str):
        try:
            decoded = json.loads(base64.b64decode(raw).decode("utf-8"))
            if isinstance(decoded, dict):
                payload = decoded.get("data") if isinstance(decoded.get("data"), dict) else decoded
        except Exception:  # noqa: BLE001 - a corrupt payload is exactly what a DLQ receives
            payload = {}
    bucket = str(payload.get("bucket") or attributes.get("bucketId") or "")
    path = str(payload.get("name") or attributes.get("objectId") or "")
    return bucket, path, attributes


def process(envelope: dict[str, Any]) -> dict[str, Any]:
    bucket, path, attributes = _object_ref(envelope)
    delivery_attempt = envelope.get("deliveryAttempt")
    log.error(
        "dead_letter_received",
        bucket=bucket,
        path=path,
        attempts=delivery_attempt,
        event_type=attributes.get("eventType"),
    )

    parsed = gcs.parse_object_path(path) if path else None
    if not parsed:
        # Nothing to attribute it to — still recorded, because a blind spot here is a lost photo.
        log.error("dead_letter_unattributable", bucket=bucket, path=path)
        return {"ok": True, "skipped": "unattributable"}
    event_id, media_id = parsed

    reason = "intake failed repeatedly and the finalize event was dead-lettered"
    outcome = _quarantine_doc(fs.db().transaction(), fs.media_ref(event_id, media_id), reason)
    if outcome == "quarantined":
        fs.ops_alert(
            event_id,
            "intake_dead_letter",
            reason,
            media_id=media_id,
            severity="error",
            objectPath=path,
        )
    else:
        log.warn(
            "dead_letter_ignored",
            event_id=event_id,
            media_id=media_id,
            reason=outcome,
        )
    return {"ok": True, "action": outcome, "eventId": event_id, "mediaId": media_id}
