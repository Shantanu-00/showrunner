"""End-to-end smoke test of the upload spine — the real code path, not a simulation.

Anonymous Firebase sign-in → `POST /uploads` → signed-URL PUT straight to GCS → Eventarc →
intake → Cloud Tasks → `worker-curate` → Firestore. Exactly what the PWA will do in S3, which is
why this stays useful afterwards as the warm-up probe in spec 09 §5's runbook.

The generated photo carries **GPS EXIF and a DateTimeOriginal**, so one run checks the two things
most likely to be quietly wrong: that capture time (not upload time) drives the temporal prior,
and that GPS does not survive in the stored original.

This script owns the *spine*: intake, renders, EXIF, dedupe, idempotency and the Curator. The
stages that land in parallel with it have their own scripts — `smoke_faces.py` (identity) and
`smoke_safety.py` (Guardian, `status='indexed'`, the public path) — so what it asserts about them is
only that they do not interfere: `status` is `processing` until every stage settles and `indexed`
after, and either is correct here depending on which stage won the race. `visibility` for a Ring-2
upload of a *generated gradient* stays `pool` because the aesthetic floor is real (0.45) and a
gradient scores 0.0 — the photo that demonstrates `public` is a real photograph, in `smoke_safety.py`.

    python scripts/smoke_upload.py --event-id dev_01J...
    python scripts/smoke_upload.py --event-id dev_... --idempotency   # re-PUT, expect no change
    python scripts/smoke_upload.py --event-id dev_... --duplicate     # same bytes, fresh mediaId
    python scripts/smoke_upload.py --event-id dev_... --corrupt       # expect permanent rejection
    python scripts/smoke_upload.py --event-id dev_... --chaos 1       # inject a 500, expect a retry
"""

from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import piexif
import requests
from PIL import Image

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

# Windows picks cp1252 for a piped stdout, and the arrows in this script's output are then a
# UnicodeEncodeError two thirds of the way through a passing run.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from shared import fs, gcs  # noqa: E402
from shared.settings import MAX_KEYFRAMES, settings  # noqa: E402
from shared.ulid import new_ulid  # noqa: E402

TERMINAL = {"processing", "indexed", "rejected", "quarantined"}

#: A stage state that will not change without a replay.
SETTLED = {"done", "failed", "failed_permanent"}

#: What spec 09 §2 prices one Curator photo at, and what the classify queue's rate is derived
#: from. A run that lands far off this is a cost regression, not a pass.
CURATE_TOKENS_IN_RAIL = 1548


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


def unique_jpeg(path: Path) -> bytes:
    """A fixture photo with one corner pixel randomised, so a run is repeatable.

    Uploading the identical file twice to one event is *correctly* deduped (spec 01 §5): intake marks
    the second `duplicateOf` the first and dispatches no perception at all, so a re-run would sit
    waiting for stages that will never exist. One changed pixel changes the md5 and nothing a face
    detector, a rubric or a dignity judgment can see. Used by `smoke_faces.py` / `smoke_safety.py`,
    which upload real photographs rather than generating one.
    """
    with Image.open(path) as opened:
        image = opened.convert("RGB")
    seed = int(time.time() * 1000) % 251
    image.putpixel((0, 0), (seed, (seed * 7) % 251, (seed * 13) % 251))
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=92)
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
    api: str,
    event_id: str,
    token: str,
    media_id: str,
    data: bytes,
    ring: str,
    *,
    content_type: str = "image/jpeg",
    file_name: str | None = None,
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
                    "fileName": file_name or f"{media_id}.jpg",
                    "contentType": content_type,
                    "size": len(data),
                }
            ],
        },
        timeout=60,
    )
    if resp.status_code != 200:
        fail(f"POST /uploads failed ({resp.status_code}): {resp.text[:400]}")
    return resp.json()["uploads"][0]


def put_bytes(url: str, data: bytes, content_type: str = "image/jpeg") -> None:
    resp = requests.put(
        url,
        data=data,
        headers={"Content-Type": content_type, "Content-Length": str(len(data))},
        timeout=300,
    )
    if resp.status_code not in (200, 201):
        fail(f"signed PUT rejected ({resp.status_code}): {resp.text[:400]}")


def put_resumable(session_uri: str, data: bytes, content_type: str) -> None:
    """Finish a GCS resumable session in one range (spec 01 §2.2's video path).

    The PWA chunks at 16 MiB because a phone on venue wifi needs to survive a dropped connection
    mid-upload; a smoke test on a wired machine has nothing to prove there, so it sends one range and
    checks the terminal status. What this *does* exercise, and what a signed PUT would not, is that
    `POST /uploads` issued a `resumableSessionUri` at all for a video content type.
    """
    total = len(data)
    resp = requests.put(
        session_uri,
        data=data,
        headers={
            "Content-Type": content_type,
            "Content-Range": f"bytes 0-{total - 1}/{total}",
        },
        timeout=600,
    )
    if resp.status_code not in (200, 201):
        fail(f"resumable PUT rejected ({resp.status_code}): {resp.text[:400]}")


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


def wait_for_stage(event_id: str, media_id: str, stage: str, timeout: float) -> dict[str, Any]:
    """Poll until `stages.{stage}` settles. Reports attempts as they climb, so a retry is visible."""
    ref = fs.media_ref(event_id, media_id)
    started = time.time()
    last: tuple[str, int] = ("", 0)
    while time.time() - started < timeout:
        doc: dict[str, Any] = ref.get().to_dict() or {}
        state = str((doc.get("stages") or {}).get(stage) or "")
        attempts = int((doc.get("attempts") or {}).get(stage) or 0)
        if (state, attempts) != last:
            print(f"      stages.{stage}={state or '(none)'} attempts={attempts}  t+{time.time() - started:.1f}s")
            last = (state, attempts)
        if state in SETTLED:
            return doc
        time.sleep(1.0)
    fail(
        f"timed out after {timeout:.0f}s waiting for stages.{stage} (last={last[0]!r}). "
        "If it never left 'pending', intake had no worker URL to dispatch to — redeploy intake "
        "after worker-curate exists (deploy/up.sh does this in the right order)."
    )
    return {}


def object_exists(bucket: str, path: str) -> bool:
    return gcs.get_blob(bucket, path) is not None


def stage_ms(doc: dict[str, Any], stage: str) -> tuple[int | None, int | None]:
    """(worker time, end-to-end time) in ms, from the stage's own timestamps.

    Worker time is `startedAt`→`doneAt` — the number spec 09 §2's budget is about. End-to-end adds
    the queue hop and, on a scaled-to-zero service, a cold start, which is why they are reported
    separately rather than averaged into one misleading figure.
    """
    timings = (doc.get("stageTimings") or {}).get(stage) or {}

    def delta(a: str, b: str) -> int | None:
        start, end = timings.get(a), timings.get(b)
        if not isinstance(start, dt.datetime) or not isinstance(end, dt.datetime):
            return None
        return int((end - start).total_seconds() * 1000)

    return delta("startedAt", "doneAt"), delta("queuedAt", "doneAt")


def check_curate(
    doc: dict[str, Any],
    event: dict[str, Any],
    expect_visibility: str,
    *,
    synthetic: bool,
) -> None:
    """Assert the Curator's contract on the stored document (spec 03 §5.1)."""
    state = (doc.get("stages") or {}).get("curate")
    if state != "done":
        reason = (doc.get("stageErrors") or {}).get("curate")
        fail(f"stages.curate is {state!r}, expected 'done' (error={reason!r})")
    ok("stages.curate=done")

    curator = doc.get("curator") or {}
    if not curator:
        fail("stages.curate=done but there is no `curator` block on the document")

    aesthetic = curator.get("aestheticScore")
    if not isinstance(aesthetic, (int, float)) or not 0.0 <= float(aesthetic) <= 1.0:
        fail(f"curator.aestheticScore is {aesthetic!r}, expected a float in [0,1]")

    # The generated photo is a gradient of nothing, so a *low* score is the correct answer. A high
    # one means the rubric anchors are not landing — which is a real regression, since `publicFloor`
    # is an absolute threshold. Skipped for `--file`, where the right score is unknown.
    if synthetic and float(aesthetic) > 0.5:
        fail(f"curator.aestheticScore={aesthetic} on a synthetic gradient — the rubric is not landing")
    ok(f"aestheticScore={aesthetic} isHighlight={curator.get('isHighlight')} tags={curator.get('momentTags')}")

    # Fusion: the stored stageId must be the argmax of a normalised posterior over stages this
    # event actually has — a stage name the event does not own means the label space leaked.
    stage_ids = [s.get("stageId") for s in (event.get("stages") or []) if s.get("stageId")]
    posterior = curator.get("stagePosterior") or {}
    stage_id = curator.get("stageId")
    if stage_ids:
        stray = sorted(set(posterior) - set(stage_ids))
        if stray:
            fail(f"stagePosterior contains stages this event does not have: {stray}")
        if stage_id is not None and stage_id not in stage_ids:
            fail(f"curator.stageId={stage_id!r} is not one of the event's stages {stage_ids}")
        total = sum(float(v) for v in posterior.values())
        if stage_id is None:
            # An honest "don't know": no visual evidence anywhere, so nothing is claimed.
            if total > 1e-6:
                fail(f"stageId is null but stagePosterior sums to {total:.3f} — fusion disagrees with itself")
            ok("stageId=null with a zero posterior (no visual evidence — honest)")
        else:
            if abs(total - 1.0) > 0.01:
                fail(f"stagePosterior sums to {total:.3f}, expected 1.0 (not normalised)")
            best = max(posterior, key=lambda k: float(posterior[k]))
            if best != stage_id:
                fail(f"curator.stageId={stage_id!r} is not the posterior argmax ({best!r})")
            ok(f"stageId={stage_id} p={float(posterior[stage_id]):.3f} (argmax of {len(posterior)} stages)")
    else:
        ok("event has no schedule — no stage attribution expected")

    glossary = set((event.get("eventTypeProfile") or {}).get("culturalGlossary") or [])
    outside = [term for term in (curator.get("culturalElements") or []) if term not in glossary]
    if outside:
        fail(f"culturalElements outside the host's glossary: {outside} (spec 11 §2)")
    ok(f"culturalElements={curator.get('culturalElements') or []} (within glossary)")

    if curator.get("needsReview"):
        fail("curator.needsReview=true — the conservative default was written, so the call failed")

    # `usage` is the item's *total* across every Gemini stage, and two of them call a model (curate
    # and safety, spec 09 §2 prices both at the same rail). So the rail check has to divide by the
    # number of model stages that have actually landed — comparing the sum against a single stage's
    # budget reported a phantom +95% cost regression the moment the Guardian shipped.
    usage = doc.get("usage") or {}
    tokens_in, tokens_out = int(usage.get("tokensIn") or 0), int(usage.get("tokensOut") or 0)
    if tokens_in <= 0:
        fail("usage.tokensIn is 0 — the cost ticker did not record the call")
    stages = doc.get("stages") or {}
    model_stages = max(1, sum(1 for s in ("curate", "safety") if stages.get(s) == "done"))
    per_stage = tokens_in / model_stages
    drift = (per_stage - CURATE_TOKENS_IN_RAIL) / CURATE_TOKENS_IN_RAIL * 100
    ok(f"usage: {tokens_in} in / {tokens_out} out across {model_stages} model stage(s) "
       f"(~{per_stage:.0f} each, {drift:+.0f}% vs the spec 09 §2 rail of {CURATE_TOKENS_IN_RAIL})")
    if drift > 15:
        fail(f"per-stage input tokens {drift:+.0f}% over the rail — the queue rates depend on it")

    if doc.get("visibility") != expect_visibility:
        fail(f"visibility={doc.get('visibility')!r}, expected {expect_visibility!r}")
    ok(f"visibility={expect_visibility} (written by recompute_visibility in the stage transaction)")

    # `indexed` is derived from the stage map, not from this stage: the three perception stages run
    # in parallel, so whether the item is already `indexed` when the Curator's assertions run is a
    # race — what must never happen is `indexed` with a stage still outstanding.
    stages = doc.get("stages") or {}
    settled = all(state in ("done", "failed", "failed_permanent") for state in stages.values())
    if doc.get("status") == "indexed" and not all(state == "done" for state in stages.values()):
        fail(f"status=indexed with stages {stages} — the derived status is too eager")
    ok(f"status={doc.get('status')} (stages {'all settled' if settled else 'still landing'}: "
       f"{' '.join(f'{k}={v}' for k, v in sorted(stages.items()))})")

    worker, end_to_end = stage_ms(doc, "curate")
    print(f"      curate: worker {worker}ms · queued→done {end_to_end}ms")
    if worker is not None and worker > 5000:
        print("      NOTE  worker time over the 5 s milestone — check for a cold start in the logs")


def set_chaos(event_id: str, stage: str, fail_next: int) -> None:
    """Arm the failure injector for the next `fail_next` deliveries of `stage` (see shared/chaos)."""
    fs.ops_col(event_id).document("chaos").set(
        {"failNext": fail_next, "stages": [stage], "reason": "smoke test chaos injection"}
    )
    ok(f"ops/chaos armed: failNext={fail_next} stage={stage}")


# ---------------------------------------------------------------- main


# ---------------------------------------------------------------- the video path (spec 03 §4)


def make_video(workdir: str) -> bytes | None:
    """A 6-second 720p test clip with a tone, via ffmpeg. None when ffmpeg is not on PATH.

    Six seconds is chosen against `MAX_KEYFRAMES`: at 1 fps it produces 6 keyframes, which proves the
    sampler runs while staying under the cap — so a count below 12 is unambiguous rather than being
    either "the cap works" or "extraction stopped early". `testsrc2` gives moving high-contrast detail,
    so the sharpness-based poster pick has something to discriminate on.
    """
    if shutil.which("ffmpeg") is None:
        return None
    out = os.path.join(workdir, "smoke.mp4")
    proc = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-nostdin", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=6",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest",
            "-movflags", "+faststart",
            out,
        ],
        capture_output=True, text=True, timeout=180, check=False,
    )
    if proc.returncode != 0 or not os.path.exists(out):
        tail = "\n".join((proc.stderr or "").strip().splitlines()[-4:])
        fail(f"could not generate a test clip: {tail[:400]}")
    return Path(out).read_bytes()


def run_video(args: argparse.Namespace, api: str, api_key: str) -> int:
    """`--video`: the branch that did not exist until worker-video-prep did.

    What it asserts, in the order the pipeline produces it:

    1. `POST /uploads` issues a **resumable session**, not a signed PUT, for a video content type.
    2. `video_prep` reaches `done` — ffprobe read the container and every render family landed.
    3. The clip carries the *same* thumb/classify/display triple a photo does. That is the assertion
       that keeps the gallery, the lightbox and the kiosk hero free of any video branch.
    4. `keyframeUris` is populated, within the cap, and every object really exists.
    5. `durationSec` and `hasAudio` are recorded — the second because nothing in this build screens
       sound, and the document is where that gap should be visible rather than only in a spec footnote.
    6. The three downstream stages were seeded `pending` and the clip was **not** `indexed` on
       `video_prep` alone. That was the trap this worker had to avoid: `_derive_status` flips on "every
       key in the stages map is done", and every public surface filters on exactly that flag.
    7. It then does reach `indexed`, with a Curator block and a Guardian verdict — so the fan-out
       `worker-video-prep` performs itself actually landed.
    """
    cfg = settings()
    with tempfile.TemporaryDirectory(prefix="smoke-video-") as workdir:
        if args.file:
            data = Path(args.file).read_bytes()
            name = Path(args.file).name
        else:
            generated = make_video(workdir)
            if generated is None:
                fail(
                    "ffmpeg is not on PATH, so a test clip cannot be generated — "
                    "pass --file path/to/clip.mp4 instead"
                )
            data = generated
            name = "smoke.mp4"

    content_type = "video/quicktime" if name.lower().endswith(".mov") else "video/mp4"
    print(f"clip: {len(data)} bytes  {content_type}  {name}")

    token, uid = sign_in_anonymously(api_key)
    ok(f"anonymous uid {uid}")

    media_id = new_ulid()
    target = register_intent(
        api, args.event_id, token, media_id, data, args.consent,
        content_type=content_type, file_name=name,
    )
    if not target.get("resumableSessionUri"):
        fail("a video intent did not come back with a resumableSessionUri (spec 01 §2.2)")
    if target.get("signedUrl"):
        fail("a video intent came back with a signed PUT URL — the photo path was taken")
    ok(f"intent registered as a resumable session: {media_id}")

    put_resumable(target["resumableSessionUri"], data, content_type)
    ok("bytes uploaded through the resumable session")

    budget = max(args.timeout, 240.0)
    print(f"      waiting for video_prep (budget {budget:.0f}s)")
    doc = wait_for_stage(args.event_id, media_id, "video_prep", budget)
    ok("stages.video_prep=done")

    if doc.get("kind") != "video":
        fail(f"kind is {doc.get('kind')!r}, expected 'video'")

    duration = doc.get("durationSec")
    if not isinstance(duration, (int, float)) or duration <= 0:
        fail(f"durationSec missing or not positive: {duration!r}")
    ok(f"durationSec={float(duration):.2f}  hasAudio={doc.get('hasAudio')}")

    for field in ("thumbUri", "classifyUri", "displayUri", "posterUri"):
        if not doc.get(field):
            fail(f"{field} missing — a clip must carry the same renders a photo does")
    for render in ("thumb_384.webp", "classify_768.webp", "display_1600.webp"):
        path = gcs.derived_path(args.event_id, media_id, render)
        if not object_exists(cfg.derived_bucket, path):
            fail(f"missing poster render: {path}")
    ok("poster produced thumb_384 / classify_768 / display_1600, posterUri set")

    keyframes = [str(u) for u in (doc.get("keyframeUris") or [])]
    if not keyframes:
        fail("keyframeUris is empty — the Guardian would be judging one arbitrary instant")
    if len(keyframes) > MAX_KEYFRAMES:
        fail(f"{len(keyframes)} keyframes exceeds the cap of {MAX_KEYFRAMES}")
    for uri in keyframes:
        parsed = gcs.parse_gs_uri(uri)
        if parsed is None or not object_exists(parsed[0], parsed[1]):
            fail(f"keyframe object missing: {uri}")
    ok(f"{len(keyframes)} keyframes present (cap {MAX_KEYFRAMES}), every object exists")

    if not doc.get("proxyUri"):
        fail("proxyUri missing — playback would fall back to the full-size original")
    proxy_parsed = gcs.parse_gs_uri(str(doc["proxyUri"]))
    if proxy_parsed is None or not object_exists(proxy_parsed[0], proxy_parsed[1]):
        fail(f"proxy object missing: {doc['proxyUri']}")
    ok("proxy_720.mp4 present")

    for stage in ("curate", "faces", "safety"):
        if (doc.get("stages") or {}).get(stage) is None:
            fail(f"stages.{stage} was never seeded — the fan-out flags did not ride the settle")
    if doc.get("status") == "indexed":
        fail("status=indexed straight after video_prep — perception had not run yet")
    ok("curate/faces/safety seeded pending; status not yet indexed")

    print(f"      waiting for perception to finish (budget {budget:.0f}s)")
    doc = wait_for(args.event_id, media_id, budget)
    if doc.get("status") != "indexed":
        fail(f"clip never reached indexed: status={doc.get('status')!r} stages={doc.get('stages')}")
    if not (doc.get("curator") or {}):
        fail("no curator block — the Curator never saw the poster")
    verdict = (doc.get("guardian") or {}).get("verdict")
    if not verdict:
        fail("no guardian verdict — the clip reached indexed without a safety answer")
    aesthetic = float((doc.get("curator") or {}).get("aestheticScore") or 0.0)
    ok(f"indexed  aesthetic={aesthetic:.2f}  guardian={verdict}")
    ok(f"visibility={doc.get('visibility')!r}  faces={len(doc.get('faces') or [])}")

    print()
    print(f"PASS  {args.event_id}/{media_id} (video)")
    return 0


def main() -> int:
    # Before the parser, not after: the flag defaults below read the environment, and `.env` is
    # only merged into it as a side effect of building Settings. Reversed, `make smoke` sees none
    # of the values `deploy/up.sh` wrote and fails claiming they were never set.
    cfg = settings()

    ap = argparse.ArgumentParser(description="Smoke-test the upload → intake → Curator spine.")
    ap.add_argument("--event-id", default=os.environ.get("SMOKE_EVENT_ID"))
    ap.add_argument("--api", default=os.environ.get("NEXT_PUBLIC_API_URL"))
    ap.add_argument("--file", default=None, help="upload a real photo instead of a generated one")
    ap.add_argument("--consent", default="pool", choices=["self", "pool", "public"])
    ap.add_argument("--timeout", type=float, default=120.0)
    ap.add_argument("--no-gps", action="store_true", help="generate without a GPS IFD")
    ap.add_argument("--idempotency", action="store_true", help="re-PUT the same bytes afterwards")
    ap.add_argument("--duplicate", action="store_true", help="upload the same bytes twice under two ids")
    ap.add_argument("--corrupt", action="store_true", help="upload truncated bytes (expect rejected)")
    ap.add_argument("--skip-curate", action="store_true", help="stop at intake, do not wait for the Curator")
    ap.add_argument(
        "--video",
        action="store_true",
        help="exercise the video path end to end (spec 03 §4): resumable upload → video_prep → "
        "poster/keyframes/proxy → the ordinary photo fan-out. Generates a 6 s clip with ffmpeg, "
        "or use --file for a real one.",
    )
    ap.add_argument(
        "--chaos",
        type=int,
        default=0,
        metavar="N",
        help="make the first N curate deliveries fail with a 500, then expect the retries to win",
    )
    args = ap.parse_args()

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

    if args.video:
        # Its own branch rather than a flag threaded through the photo path: a clip takes a resumable
        # session instead of a signed PUT, has no EXIF or GPS to assert, and gains four assertions
        # (poster, keyframes, proxy, the deferred index) that mean nothing for a still.
        for unsupported in ("idempotency", "duplicate", "corrupt", "no_gps"):
            if getattr(args, unsupported, False):
                fail(f"--{unsupported.replace('_', '-')} is a photo-path test; drop it or drop --video")
        return run_video(args, api, api_key)

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

    if args.chaos:
        if event.get("class") not in ("protected_demo", "internal_dev"):
            fail(f"--chaos needs a protected_demo/internal_dev event; this one is {event.get('class')!r}")
        set_chaos(args.event_id, "curate", args.chaos)

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

    if not args.skip_curate:
        # Cloud Tasks' minimum backoff is 10 s (spec 09 §2), so each injected failure costs at
        # least that before the retry that succeeds — the budget has to grow with the injections.
        budget = args.timeout + args.chaos * 60.0
        print(f"      waiting for the Curator (budget {budget:.0f}s)")
        doc = wait_for_stage(args.event_id, media_id, "curate", budget)
        check_curate(
            doc,
            event,
            "self" if args.consent == "self" else "pool",
            synthetic=not args.file,
        )

        if args.chaos:
            attempts = int((doc.get("attempts") or {}).get("curate") or 0)
            if attempts <= args.chaos:
                fail(f"attempts.curate={attempts} after {args.chaos} injected failures — chaos did not fire")
            ok(f"survived {args.chaos} injected 500s: attempts.curate={attempts}, stage still done")
            # Disarm, so a later run against this event is not silently testing chaos.
            fs.ops_col(args.event_id).document("chaos").delete()
            ok("ops/chaos cleared")

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
