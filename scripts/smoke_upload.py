"""End-to-end smoke test of the upload spine — the real code path, not a simulation.

Anonymous Firebase sign-in → `POST /uploads` → signed-URL PUT straight to GCS → Eventarc →
intake → Firestore. Exactly what the PWA will do in S3, which is why this stays useful
afterwards as the warm-up probe in spec 09 §5's runbook.

The generated photo carries **GPS EXIF and a DateTimeOriginal**, so one run checks the two things
most likely to be quietly wrong: that capture time (not upload time) drives the temporal prior,
and that GPS does not survive in the stored original.

    python scripts/smoke_upload.py --event-id dev_01J...
    python scripts/smoke_upload.py --event-id dev_... --idempotency   # re-PUT, expect no change
    python scripts/smoke_upload.py --event-id dev_... --duplicate     # same bytes, fresh mediaId
    python scripts/smoke_upload.py --event-id dev_... --corrupt       # expect permanent rejection
"""

from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import os
import random
import sys
import time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import piexif
import requests
from PIL import Image

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

from shared import fs, gcs  # noqa: E402
from shared.settings import settings  # noqa: E402
from shared.ulid import new_ulid  # noqa: E402

TERMINAL = {"processing", "indexed", "rejected", "quarantined"}


def fail(message: str) -> None:
    print(f"FAIL  {message}")
    raise SystemExit(1)


def ok(message: str) -> None:
    print(f"ok    {message}")


# ---------------------------------------------------------------- fixtures


def make_photo(captured_local: dt.datetime, *, seed: int, with_gps: bool = True) -> bytes:
    """A JPEG with DateTimeOriginal and (optionally) a GPS IFD, unique per seed.

    Unique pixels matter: identical bytes would hit the md5 dedupe register and the run would
    test deduplication instead of ingestion.
    """
    rng = random.Random(seed)
    img = Image.new("RGB", (1600, 1200))
    pixels = img.load()
    base = (rng.randint(40, 200), rng.randint(40, 200), rng.randint(40, 200))
    for y in range(0, 1200, 4):
        for x in range(0, 1600, 4):
            shade = (base[0] + x // 12) % 256, (base[1] + y // 12) % 256, base[2]
            for dy in range(4):
                for dx in range(4):
                    pixels[x + dx, y + dy] = shade

    stamp = captured_local.strftime("%Y:%m:%d %H:%M:%S").encode()
    exif: dict[str, Any] = {
        "0th": {
            piexif.ImageIFD.Make: b"Showrunner",
            piexif.ImageIFD.Model: b"Smoke Test",
            piexif.ImageIFD.DateTime: stamp,
        },
        "Exif": {
            piexif.ExifIFD.DateTimeOriginal: stamp,
            piexif.ExifIFD.DateTimeDigitized: stamp,
        },
        "GPS": {},
        "1st": {},
        "thumbnail": None,
    }
    if with_gps:
        # Pune, roughly. If this survives into the stored original, the privacy claim is false.
        exif["GPS"] = {
            piexif.GPSIFD.GPSLatitudeRef: b"N",
            piexif.GPSIFD.GPSLatitude: ((18, 1), (31, 1), (0, 1)),
            piexif.GPSIFD.GPSLongitudeRef: b"E",
            piexif.GPSIFD.GPSLongitude: ((73, 1), (51, 1), (0, 1)),
        }

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88, exif=piexif.dump(exif))
    return buf.getvalue()


# ---------------------------------------------------------------- steps


def sign_in_anonymously(api_key: str) -> tuple[str, str]:
    resp = requests.post(
        f"https://identitytoolkit.googleapis.com/v1/accounts:signUp?key={api_key}",
        json={"returnSecureToken": True},
        timeout=30,
    )
    if resp.status_code != 200:
        fail(f"anonymous sign-in failed ({resp.status_code}): {resp.text[:300]}")
    body = resp.json()
    return body["idToken"], body["localId"]


def register_intent(
    api: str, event_id: str, token: str, media_id: str, data: bytes, ring: str
) -> dict[str, Any]:
    consent = {"public": ring == "public", "selfOnly": ring == "self"}
    resp = requests.post(
        f"{api}/v1/events/{event_id}/uploads",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "batchId": new_ulid(),
            "consent": consent,
            "files": [
                {
                    "clientMediaId": media_id,
                    "fileName": f"{media_id}.jpg",
                    "contentType": "image/jpeg",
                    "size": len(data),
                }
            ],
        },
        timeout=60,
    )
    if resp.status_code != 200:
        fail(f"POST /uploads failed ({resp.status_code}): {resp.text[:400]}")
    return resp.json()["uploads"][0]


def put_bytes(url: str, data: bytes) -> None:
    resp = requests.put(
        url,
        data=data,
        headers={"Content-Type": "image/jpeg", "Content-Length": str(len(data))},
        timeout=120,
    )
    if resp.status_code not in (200, 201):
        fail(f"signed PUT rejected ({resp.status_code}): {resp.text[:400]}")


def wait_for(event_id: str, media_id: str, timeout: float) -> dict[str, Any]:
    ref = fs.media_ref(event_id, media_id)
    started = time.time()
    last = ""
    while time.time() - started < timeout:
        snap = ref.get()
        doc: dict[str, Any] = (snap.to_dict() or {}) if snap.exists else {}
        status = str(doc.get("status") or "")
        if status != last:
            print(f"      status={status or '(none)'}  t+{time.time() - started:.1f}s")
            last = status
        if status in TERMINAL:
            return doc
        time.sleep(1.0)
    fail(f"timed out after {timeout:.0f}s waiting for {media_id} (last status={last!r})")
    return {}


def object_exists(bucket: str, path: str) -> bool:
    return gcs.get_blob(bucket, path) is not None


# ---------------------------------------------------------------- main


def main() -> int:
    ap = argparse.ArgumentParser(description="Smoke-test the upload → intake spine.")
    ap.add_argument("--event-id", default=os.environ.get("SMOKE_EVENT_ID"))
    ap.add_argument("--api", default=os.environ.get("NEXT_PUBLIC_API_URL"))
    ap.add_argument("--file", default=None, help="upload a real photo instead of a generated one")
    ap.add_argument("--consent", default="pool", choices=["self", "pool", "public"])
    ap.add_argument("--timeout", type=float, default=120.0)
    ap.add_argument("--no-gps", action="store_true", help="generate without a GPS IFD")
    ap.add_argument("--idempotency", action="store_true", help="re-PUT the same bytes afterwards")
    ap.add_argument("--duplicate", action="store_true", help="upload the same bytes twice under two ids")
    ap.add_argument("--corrupt", action="store_true", help="upload truncated bytes (expect rejected)")
    args = ap.parse_args()

    cfg = settings()
    api = (args.api or "").rstrip("/")
    api_key = os.environ.get("NEXT_PUBLIC_FIREBASE_API_KEY", "")
    if not args.event_id:
        fail("no event — pass --event-id or set SMOKE_EVENT_ID (see scripts/dev_event.py)")
    if not api:
        fail("no API URL — pass --api or set NEXT_PUBLIC_API_URL")
    if not api_key:
        fail("no NEXT_PUBLIC_FIREBASE_API_KEY — run ./deploy/bootstrap.sh")

    event = fs.get_event(args.event_id)
    if not event:
        fail(f"event {args.event_id} does not exist")
    tz = ZoneInfo(str(event.get("timezone") or "UTC"))
    print(f"event {args.event_id}  status={event.get('status')}  tz={tz}")

    health = requests.get(f"{api}/livez", timeout=30)
    if health.status_code != 200:
        fail(f"api unhealthy: {health.status_code} {health.text[:200]}")
    ok(f"api healthy: {health.json()}")

    # Capture time is 30 minutes ago *in the event's timezone* — inside the active stage window.
    captured_local = (dt.datetime.now(tz) - dt.timedelta(minutes=30)).replace(microsecond=0)
    seed = int(time.time())
    if args.file:
        data = Path(args.file).read_bytes()
    else:
        data = make_photo(captured_local.replace(tzinfo=None), seed=seed, with_gps=not args.no_gps)
    if args.corrupt:
        # Valid JPEG header, truncated payload: decodes far enough to look real, then fails.
        data = data[: len(data) // 3]
    print(f"photo: {len(data)} bytes  capturedLocal={captured_local.isoformat()}")

    token, uid = sign_in_anonymously(api_key)
    ok(f"anonymous uid {uid}")

    media_id = new_ulid()
    target = register_intent(api, args.event_id, token, media_id, data, args.consent)
    ok(f"intent registered: {media_id} → {target['objectPath']}")

    put_bytes(target["signedUrl"], data)
    ok("bytes uploaded through the signed URL")

    doc = wait_for(args.event_id, media_id, args.timeout)
    status = doc.get("status")

    if args.corrupt:
        if status != "rejected":
            fail(f"corrupt file should be rejected, got status={status}")
        ok(f"corrupt file rejected permanently (reason={doc.get('rejectedReason')})")
        if object_exists(cfg.raw_bucket, target["objectPath"]):
            fail("rejected object was not deleted from the raw bucket")
        ok("rejected object deleted from raw")
        return 0

    if status not in ("processing", "indexed"):
        fail(f"unexpected status {status}: {json.dumps({k: str(v) for k, v in doc.items()})[:500]}")
    ok(f"status={status}")

    if doc.get("stages", {}).get("thumb") != "done":
        fail(f"stages.thumb is {doc.get('stages', {}).get('thumb')!r}, expected 'done'")
    ok("stages.thumb=done")

    captured = doc.get("capturedAt")
    if not isinstance(captured, dt.datetime):
        fail(f"capturedAt missing or not a timestamp: {captured!r}")
    if args.no_gps or args.file:
        ok(f"capturedAt={captured.isoformat()}")
    else:
        drift = abs((captured.astimezone(dt.timezone.utc) - captured_local.astimezone(dt.timezone.utc)).total_seconds())
        if doc.get("exifMissing"):
            fail("exifMissing=true — EXIF DateTimeOriginal was not read")
        if drift > 90:
            fail(f"capturedAt drifted {drift:.0f}s from EXIF — timezone interpretation is wrong")
        ok(f"capturedAt={captured.isoformat()} (EXIF, interpreted in {tz})")

    for name in ("thumb_384.webp", "classify_768.webp", "display_1600.webp"):
        path = gcs.derived_path(args.event_id, media_id, name)
        if not object_exists(cfg.derived_bucket, path):
            fail(f"missing derived render: {path}")
    ok("three derived renders present")

    if not (args.no_gps or args.file):
        raw = gcs.download_bytes(cfg.raw_bucket, target["objectPath"])
        gps = piexif.load(raw).get("GPS") or {}
        if gps:
            fail(f"GPS survived in the stored original: {list(gps)}")
        ok("GPS IFD removed from the stored original")
        original = piexif.load(data)["Exif"].get(piexif.ExifIFD.DateTimeOriginal)
        kept = piexif.load(raw)["Exif"].get(piexif.ExifIFD.DateTimeOriginal)
        if original != kept:
            fail("DateTimeOriginal changed during the GPS strip — the rewrite was not lossless")
        ok("DateTimeOriginal preserved (lossless rewrite)")

    if args.idempotency:
        before = {k: str(v) for k, v in (fs.media_ref(args.event_id, media_id).get().to_dict() or {}).items()}
        refreshed = requests.post(
            f"{api}/v1/events/{args.event_id}/uploads/{media_id}/refresh-url",
            headers={"Authorization": f"Bearer {token}"},
            timeout=60,
        )
        if refreshed.status_code == 409:
            ok("refresh-url on a processed media → 409 ALREADY_PROCESSED (expected)")
            # Re-upload through a fresh signed URL for a brand-new mediaId is a different test;
            # here we re-PUT the same object path directly using a fresh intent-free signed URL.
            url, _ = gcs.signed_put_url(
                cfg.raw_bucket, target["objectPath"], content_type="image/jpeg", content_length=len(data)
            )
            put_bytes(url, data)
        else:
            put_bytes(refreshed.json()["upload"]["signedUrl"], data)
        time.sleep(20)
        after = {k: str(v) for k, v in (fs.media_ref(args.event_id, media_id).get().to_dict() or {}).items()}
        changed = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
        if changed:
            fail(f"duplicate delivery changed fields: {sorted(changed)}")
        ok("duplicate finalize delivery changed nothing (transaction guard held)")

    if args.duplicate:
        dup_id = new_ulid()
        dup_target = register_intent(api, args.event_id, token, dup_id, data, args.consent)
        put_bytes(dup_target["signedUrl"], data)
        dup = wait_for(args.event_id, dup_id, args.timeout)
        if dup.get("duplicateOf") != media_id:
            fail(f"duplicateOf is {dup.get('duplicateOf')!r}, expected {media_id}")
        ok(f"byte-identical upload marked duplicateOf={media_id}, no perception dispatched")

    print()
    print(f"PASS  {args.event_id}/{media_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
