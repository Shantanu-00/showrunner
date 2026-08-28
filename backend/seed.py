"""Seed a demo event with the AI wedding cast + ~25 golden fixtures, through the real pipeline.

    python backend/seed.py --event demo
    python backend/seed.py --event demo --regen-cast   # force fresh Nano Banana portraits

This is `make seed`'s target (session B2-S7). It is deliberately not a fixture loader: every photo
goes through the same `POST /uploads` -> signed PUT -> Eventarc -> Curator/Face/Guardian path a
real guest's phone would use, because that is the only path whose output `eval/run_eval.py` can
honestly grade. `make eval` is the read side of this; this is the write side.

The cast (bride, groom, groom's mother, three guests — `eval/cast.py`) is enrolled as real `people`
documents, not just uploaded as photos: the bride and groom are host-declared PRINCIPAL VIPs, the
mother INNER_CIRCLE (spec 11 §3), seeded directly the way a host's pre-event guest list would be,
and the three guests self-enroll through the ordinary `/people` selfie path. This is also the full
AI cast the video plan needs for the group-photo, bounty-target and reel beats (HANDOFF §7b).
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import io
import json
import os
import sys
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

BACKEND = Path(__file__).resolve().parent
EVAL = Path(__file__).resolve().parents[1] / "eval"
SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(EVAL))
sys.path.insert(0, str(SCRIPTS))

import piexif  # noqa: E402
import requests  # noqa: E402
from PIL import Image  # noqa: E402

import cast as cast_module  # noqa: E402
import fixtures as fixtures_module  # noqa: E402
import dev_event  # noqa: E402
from schemas.event import DemoConfig, Event, EventClass, EventStatus, EventTemplateId  # noqa: E402
from schemas.event import EventTypeProfile, SensitivityProfile, VipTopology  # noqa: E402
from schemas.person import Tier  # noqa: E402
from shared import fs, internal as face_internal  # noqa: E402
from shared.settings import settings  # noqa: E402
from shared.ulid import new_ulid  # noqa: E402

from smoke_safety import wait_for_indexed  # noqa: E402
from smoke_upload import put_bytes, register_intent, sign_in_anonymously  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ARTIFACTS = EVAL / "artifacts"
RUN_FILE = ARTIFACTS / "seed_run.json"

_TIER_BY_LABEL = {
    "principal": Tier.PRINCIPAL,
    "inner_circle": Tier.INNER_CIRCLE,
    "guest": Tier.GUEST,
}


def log(message: str) -> None:
    print(f"      {message}")


def fail(message: str) -> None:
    print(f"FAIL  {message}")
    raise SystemExit(1)


# ---------------------------------------------------------------- event


def reset_event(event_id: str) -> None:
    """Wipe media/people/enrollments/claimAudits/ops/hashes before reseeding (the `make demo-reset`
    video dependency, HANDOFF §7b — a seed run has to be re-runnable without hand-cleanup, and
    without it a retry after a partial failure duplicates every VIP person doc it re-enrolls).

    `hashes` matters more than it looks: cast fixtures reuse cached portrait bytes and
    `captured_local` is derived from an hour-rounded anchor, so re-seeding twice inside the same
    clock hour reproduces byte-identical uploads. Leaving a stale `hashes/{md5}` register pointing
    at a mediaId this reset just deleted makes intake mark the reseeded item `duplicateOf` a ghost
    — `stages` stops at `thumb`, `status` is forced straight to `indexed` (intake/app.py's
    duplicate branch), and Curator/Guardian never run. Found live, this session, exactly that way.
    """
    for collection in (fs.media_col, fs.people_col, fs.enrollments_col, fs.claim_audits_col, fs.ops_col, fs.hashes_col):
        docs = list(collection(event_id).stream())
        for doc in docs:
            doc.reference.delete()
        if docs:
            log(f"reset: cleared {len(docs)} doc(s) from {collection(event_id).id}")


def ensure_event(event_id: str, timezone: str) -> dict[str, Any]:
    """Same shape as `scripts/dev_event.py`, at a stable id — re-anchored to `now` on every run
    so a re-seed keeps its fixtures' relative capture offsets inside the current stage windows."""
    tz = ZoneInfo(timezone)
    now = dt.datetime.now(dt.timezone.utc)
    event = Event(
        eventId=event_id,
        name="Showrunner Eval/Demo Wedding",
        timezone=timezone,
        status=EventStatus.LIVE,
        **{"class": EventClass.INTERNAL_DEV},
        stages=dev_event.build_stages(now, tz),
        activeStage="sangeet",
        eventTypeProfile=EventTypeProfile(
            templateId=EventTemplateId.WEDDING_HINDU,
            vipTopology=VipTopology.PYRAMID,
            sensitivityProfile=SensitivityProfile(),
            culturalGlossary=["haldi", "sangeet", "kanyadaan", "baraat", "mangalsutra"],
        ),
        demoConfig=DemoConfig(enabled=True, compressedTimeline=True),
        createdAt=now,
        liveAt=now,
    )
    payload = dev_event.firestore_ready(event.model_dump(by_alias=True))
    fs.event_ref(event_id).set(payload, merge=True)
    return fs.get_event(event_id) or {}


# ---------------------------------------------------------------- cast enrollment


def stamp_captured_at(image_bytes: bytes, captured_local: dt.datetime) -> bytes:
    """Re-encode arbitrary image bytes as a JPEG carrying `captured_local` as DateTimeOriginal."""
    with Image.open(io.BytesIO(image_bytes)) as opened:
        rgb = opened.convert("RGB")
    stamp = captured_local.replace(tzinfo=None).strftime("%Y:%m:%d %H:%M:%S").encode()
    exif = {
        "0th": {piexif.ImageIFD.Make: b"Showrunner", piexif.ImageIFD.DateTime: stamp},
        "Exif": {piexif.ExifIFD.DateTimeOriginal: stamp, piexif.ExifIFD.DateTimeDigitized: stamp},
    }
    buf = io.BytesIO()
    rgb.save(buf, format="JPEG", quality=90, exif=piexif.dump(exif))
    return buf.getvalue()


def seed_person(
    event_id: str, embedding: list[float], display_name: str, tier: Tier, host_enrolled: bool
) -> str:
    """Direct Firestore seed, mirroring a host's pre-event VIP list (spec 11 §3) — the same shape
    `scripts/smoke_faces.py::seed_vip` writes, parameterized by tier."""
    person_id = new_ulid()
    now = dt.datetime.now(dt.timezone.utc)
    fs.enrollment_ref(event_id, person_id).set({"personId": person_id, "embedding": embedding, "createdAt": now})
    fs.person_ref(event_id, person_id).set(
        {
            "personId": person_id,
            "displayName": display_name,
            "uidLinks": [],
            "tier": int(tier),
            "hostEnrolled": host_enrolled,
            "featured": False,
            "consent": {"selfieEnrolled": True, "enrolledAt": now, "retentionNoticeShown": True},
            "tasteProfile": {},
            "createdAt": now,
        }
    )
    return person_id


def enroll_self(api: str, api_key: str, event_id: str, selfie_b64: str, display_name: str) -> str | None:
    token, _uid = sign_in_anonymously(api_key)
    resp = requests.post(
        f"{api}/v1/events/{event_id}/people",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "selfie": selfie_b64,
            "displayName": display_name,
            "biometricConsent": True,
            "retentionNoticeShown": True,
        },
        timeout=60,
    )
    if resp.status_code != 200:
        log(f"WARN  self-enroll for {display_name} failed ({resp.status_code}): {resp.text[:200]}")
        return None
    return resp.json().get("personId")


def enroll_cast(api: str, api_key: str, event_id: str, members: list[cast_module.CastMember]) -> list[dict[str, Any]]:
    enrolled = []
    for member in members:
        if member.tier == "guest":
            selfie_b64 = base64.b64encode(member.photo.read_bytes()).decode("ascii")
            person_id = enroll_self(api, api_key, event_id, selfie_b64, member.displayName)
        else:
            selfie_b64 = base64.b64encode(member.photo.read_bytes()).decode("ascii")
            try:
                body = face_internal.embed_selfie(selfie_b64, max_faces=1)
            except face_internal.FaceServiceError as exc:
                log(f"WARN  embedding failed for {member.slug}: {exc}")
                continue
            faces = body.get("faces") or []
            if not faces:
                log(f"WARN  no face detected in {member.slug}'s portrait")
                continue
            person_id = seed_person(
                event_id, faces[0]["embedding"], member.displayName, _TIER_BY_LABEL[member.tier], host_enrolled=True
            )
        if person_id:
            enrolled.append({"personId": person_id, "slug": member.slug, "displayName": member.displayName, "tier": member.tier})
            log(f"cast enrolled: {member.displayName} ({member.tier}) -> {person_id}")
    return enrolled


# ---------------------------------------------------------------- fixture upload


def upload_fixture(
    api: str, api_key: str, event_id: str, tz: ZoneInfo, fixture: fixtures_module.Fixture, timeout: float
) -> dict[str, Any]:
    now = dt.datetime.now(dt.timezone.utc)
    anchor = now.astimezone(tz).replace(minute=0, second=0, microsecond=0)
    captured_local = anchor + dt.timedelta(hours=fixture.captureOffsetHours)

    raw = fixture.imageBytes()
    data = stamp_captured_at(raw, captured_local)

    token, _uid = sign_in_anonymously(api_key)
    media_id = new_ulid()
    target = register_intent(api, event_id, token, media_id, data, fixture.consent)
    put_bytes(target["signedUrl"], data)
    log(f"{fixture.fixtureId}: uploaded ({len(data)} bytes, capturedLocal={captured_local.isoformat()})")

    doc = wait_for_indexed(event_id, media_id, timeout)
    return {"fixtureId": fixture.fixtureId, "mediaId": media_id, "status": doc.get("status")}


# ---------------------------------------------------------------- main


def main() -> int:
    cfg = settings()
    cfg.require("project")

    ap = argparse.ArgumentParser(description="Seed a demo/eval event through the real pipeline.")
    ap.add_argument("--event", default="demo")
    ap.add_argument("--timezone", default="Asia/Kolkata")
    ap.add_argument("--api", default=os.environ.get("NEXT_PUBLIC_API_URL"))
    ap.add_argument("--timeout", type=float, default=150.0)
    ap.add_argument("--regen-cast", action="store_true")
    ap.add_argument("--skip-cast", action="store_true", help="upload fixtures only, reuse an existing cast")
    ap.add_argument("--no-reset", action="store_true", help="keep whatever is already on the event (default: wipe first)")
    ap.add_argument("--reset-only", action="store_true", help="wipe the event's people/media/ops and exit (make demo-reset)")
    args = ap.parse_args()

    event_id = f"dev_{args.event}"

    if args.reset_only:
        reset_event(event_id)
        print(f"PASS  reset {event_id}")
        return 0

    api = (args.api or "").rstrip("/")
    api_key = os.environ.get("NEXT_PUBLIC_FIREBASE_API_KEY", "")
    if not api:
        fail("no API URL — pass --api or set NEXT_PUBLIC_API_URL")
    if not api_key:
        fail("no NEXT_PUBLIC_FIREBASE_API_KEY — run ./deploy/bootstrap.sh")

    if not args.no_reset:
        reset_event(event_id)
    event = ensure_event(event_id, args.timezone)
    tz = ZoneInfo(args.timezone)
    log(f"event {event_id} ready (status={event.get('status')}, class={event.get('class')})")

    log("generating/loading the AI wedding cast (Nano Banana) —")
    members = cast_module.ensure_cast(regenerate=args.regen_cast)

    cast_records: list[dict[str, Any]] = []
    if not args.skip_cast:
        log("enrolling cast as people —")
        cast_records = enroll_cast(api, api_key, event_id, members)

    fixture_list = fixtures_module.build_fixtures(members)
    log(f"uploading {len(fixture_list)} golden fixtures through the real pipeline —")
    items = []
    for fixture in fixture_list:
        try:
            items.append(upload_fixture(api, api_key, event_id, tz, fixture, args.timeout))
        except SystemExit:
            raise
        except Exception as exc:  # a stuck fixture should not abort the whole seed run
            log(f"WARN  {fixture.fixtureId} failed to seed: {exc}")
            items.append({"fixtureId": fixture.fixtureId, "mediaId": None, "error": str(exc)})

    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    RUN_FILE.write_text(
        json.dumps(
            {
                "eventId": event_id,
                "seededAt": dt.datetime.now(dt.timezone.utc).isoformat(),
                "cast": cast_records,
                "items": items,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    ok_count = sum(1 for i in items if i.get("mediaId"))
    print()
    print(f"PASS  seeded {ok_count}/{len(items)} fixtures into {event_id} -> {RUN_FILE}")
    print(f"      next: python eval/run_eval.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
