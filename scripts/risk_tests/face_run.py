"""Probe: does InsightFace actually run on Cloud Run, fast enough, with a baked model?

This is the one probe whose failure is a genuine emergency. Spec 03's Face Indexer is
load-bearing for private albums, bounties and reel casting; spec 09 sizes `worker-face` at
min-instances 1 on 2 vCPU / 4 GiB assuming (a) ONNX Runtime CPU is enough, (b) the 326 MB
buffalo_l bundle is baked into the image rather than downloaded on boot, and (c) a photo
embeds in well under a second. If any of those is wrong, the whole face architecture has
to change, so it gets measured on the real platform before anything is built on it.

Four questions, in descending order of how badly a wrong answer hurts:
  1. Does the container serve at all on Cloud Run (native build + ONNX on managed CPU)?
  2. Is the model BAKED — i.e. does the running process find it on disk instead of
     re-downloading 326 MB from GitHub on every cold start?
  3. Cold start and per-photo latency, measured.
  4. Are the embeddings what spec 03's Firestore vector index expects: 512-d and already
     L2-normalized, so `findNearest(DOT_PRODUCT)` behaves as cosine similarity?

Question 4 gets a bonus check the design depends on but no doc can answer: the same face
restyled (`cast_portrait.png` vs `cast_portrait_styled.png`) should still land close in
embedding space. That is exactly the "same guest, different lighting/filter" case the
matcher will face all night.

Two traps this probe had to be rewritten to avoid, because the first version fell into
both and produced numbers that looked like platform findings and weren't:

  * **Round-trip latency is not the worker's latency.** Uploading a 1.9 MB portrait from a
    home connection costs ~20 s; the real worker streams from GCS inside the same region.
    The number that transfers to production is the service's own `decode_ms + inference_ms`,
    so that is what the verdict is judged on, with round-trip reported separately and
    labelled.
  * **The first request after a deploy is not a cold start.** Cloud Run's startup probe has
    already booted an instance before traffic is routed. A real cold start is only visible
    on scale-out, which is what the concurrent burst below forces — and scale-out is
    precisely the case that matters, since spec 09 pins `worker-face` at min-instances 1.

The probe reads the deployed service; it does not deploy. Deploy first with:

    gcloud run deploy showrunner-face-probe \
      --source scripts/risk_tests/face_probe \
      --region us-central1 --project showrunner-hq \
      --cpu 2 --memory 4Gi --min-instances 0 --max-instances 4 --concurrency 1 \
      --timeout 300 --no-allow-unauthenticated

MODEL_ROOT is deliberately NOT passed as a --set-env-vars flag: Git Bash rewrites the
value `/models` into `C:/Program Files/Git/models` before gcloud ever sees it, which
silently un-bakes the model. The Dockerfile's own `ENV MODEL_ROOT` is the single source.
"""

from __future__ import annotations

import json
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import requests

import _harness as H

SERVICE = "showrunner-face-probe"
WARM_REQUESTS = 5
# Concurrency 1 on the service, so 6 simultaneous requests guarantee scale-out and with it
# at least one genuinely cold instance — the only cold start a guest can ever wait on.
BURST_REQUESTS = 6

# Judgement thresholds. Chosen from spec 09's sizing, not from vibes:
#   * worker-face runs at min-instances 1, so a cold start only ever hits a scale-out
#     event, not a guest's first photo — 90 s is the point where scale-out under a burst
#     would start dropping photos behind the Cloud Tasks retry window.
#   * spec 09 budgets face indexing inside the ~10 s intake-to-album target alongside
#     download, classify and safety, so 2 s of embed time is the ceiling that leaves room.
COLD_START_CEILING_S = 90.0
SERVER_LATENCY_CEILING_MS = 2000.0
EXPECTED_DIM = 512
# Same identity across a full restyle. ArcFace cosine thresholds sit near 0.28-0.40 for
# verification; a restyle is a hard case, so treat anything at or above the spec-03
# match threshold as the design working and flag below that as needing a lower bar.
SAME_IDENTITY_FLOOR = 0.30


def _gcloud() -> str:
    # On Windows gcloud is a .cmd shim, so bare "gcloud" is not an executable file.
    path = shutil.which("gcloud") or shutil.which("gcloud.cmd")
    if not path:
        raise RuntimeError("gcloud not on PATH — cannot resolve the service URL")
    return path


def _run_gcloud(args: list[str], timeout: int = 180) -> str:
    result = subprocess.run(
        [_gcloud(), *args], capture_output=True, text=True, timeout=timeout, shell=False
    )
    if result.returncode != 0:
        raise RuntimeError(f"gcloud {' '.join(args[:3])} failed: {result.stderr.strip()[:300]}")
    return result.stdout.strip()


def _service_url(v: H.Verdict) -> str:
    url = _run_gcloud(
        [
            "run", "services", "describe", SERVICE,
            "--region", H.location(), "--project", H.project(),
            "--format=value(status.url)",
        ]
    )
    if not url:
        raise RuntimeError(
            f"service {SERVICE} not found in {H.location()} — deploy it first "
            "(see this module's docstring)"
        )
    v.note(f"service URL: {url}")
    return url


def _auth_headers(v: H.Verdict, url: str) -> dict[str, str]:
    """Cloud Run accepts either a Google-issued ID token or an OAuth access token.

    With user ADC (no key files, per the security defaults) `print-identity-token` mints a
    token whose audience is gcloud's own client, which Cloud Run accepts for exactly this
    interactive case. Fall back to an access token if that ever tightens up.
    """
    try:
        token = _run_gcloud(["auth", "print-identity-token"], timeout=90)
        probe = requests.get(url + "/", headers={"Authorization": f"Bearer {token}"}, timeout=300)
        if probe.status_code != 403:
            return {"Authorization": f"Bearer {token}"}
        v.note("identity token rejected (403) — falling back to an ADC access token")
    except (RuntimeError, subprocess.TimeoutExpired) as exc:
        v.note(f"identity token unavailable ({exc}) — falling back to an ADC access token")
    return {"Authorization": f"Bearer {H.access_token()}"}


def _image_size(v: H.Verdict) -> None:
    """Best-effort: how big is the image we're paying to pull on every cold start?"""
    try:
        raw = _run_gcloud(
            [
                "artifacts", "docker", "images", "list",
                f"{H.location()}-docker.pkg.dev/{H.project()}/cloud-run-source-deploy/{SERVICE}",
                "--project", H.project(), "--limit", "1", "--sort-by", "~UPDATE_TIME",
                "--format=value(SIZE)",
            ]
        )
    except (RuntimeError, subprocess.TimeoutExpired) as exc:
        v.note(f"could not read the image size: {exc}")
        return
    size = raw.splitlines()[0].strip() if raw else ""
    if size.isdigit():
        v.note(f"deployed image size: {int(size) / 1e6:.0f} MB (compressed layers, pulled on cold start)")


def _embed(url: str, headers: dict[str, str], payload: bytes) -> tuple[dict, float]:
    started = time.monotonic()
    response = requests.post(
        url + "/embed",
        data=payload,
        headers={**headers, "Content-Type": "application/octet-stream"},
        timeout=300,
    )
    elapsed_ms = (time.monotonic() - started) * 1000
    if response.status_code != 200:
        raise RuntimeError(f"/embed returned {response.status_code}: {response.text[:300]}")
    return response.json(), elapsed_ms


def _cosine(a: list[float], b: list[float]) -> float:
    """Both vectors are already unit-norm, so the dot product IS cosine similarity."""
    return sum(x * y for x, y in zip(a, b))


def _burst(url: str, headers: dict[str, str], payload: bytes, v: H.Verdict) -> float | None:
    """Fire concurrent requests to force scale-out, and time the cold instance.

    Returns the worst observed round-trip on a NEW instance, in seconds, or None if the
    burst never actually caused a scale-out (in which case it measured nothing).
    """
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=BURST_REQUESTS) as pool:
        futures = [
            pool.submit(_embed, url, headers, payload) for _ in range(BURST_REQUESTS)
        ]
        outcomes = []
        for future in futures:
            try:
                outcomes.append(future.result())
            except RuntimeError as exc:
                v.note(f"burst request failed: {exc}")

    if not outcomes:
        v.note("burst produced no successful responses — cold start not measured")
        return None

    by_instance: dict[str, list[float]] = {}
    for result, elapsed_ms in outcomes:
        by_instance.setdefault(result.get("instance_id", "?"), []).append(elapsed_ms)

    v.note(
        f"burst of {BURST_REQUESTS} concurrent requests hit {len(by_instance)} instance(s); "
        f"slowest round-trip {max(ms for l in by_instance.values() for ms in l):.0f}ms"
    )
    if len(by_instance) < 2:
        v.note(
            "no scale-out occurred, so this did not measure a cold start — treat the "
            "in-container model load as the cold-start floor instead"
        )
        return None

    # The slowest request overall is the one that waited for a new instance to boot.
    worst_ms = max(ms for latencies in by_instance.values() for ms in latencies)
    return round(worst_ms / 1000, 1)


def _small_jpeg(portrait: Path, v: H.Verdict) -> bytes:
    """A ~1024px JPEG of the portrait, for the concurrent burst.

    The burst fires 6 uploads at once; at the original 1.9 MB that is 11 MB up a home
    connection, and the resulting timings would measure this room's broadband rather than
    Cloud Run's scale-out. Falls back to the original if Pillow isn't around.
    """
    try:
        import io

        from PIL import Image
    except ImportError:
        v.note("Pillow unavailable — bursting with the full-size image (timings will include upload)")
        return portrait.read_bytes()

    image = Image.open(portrait).convert("RGB")
    image.thumbnail((1024, 1024))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=88)
    payload = buffer.getvalue()
    v.note(
        f"burst payload: {portrait.name} downscaled to {image.size[0]}x{image.size[1]} JPEG, "
        f"{len(payload) / 1024:.0f} KB (keeps the burst measuring Cloud Run, not this uplink)"
    )
    return payload


def body(v: H.Verdict) -> None:
    url = _service_url(v)
    headers = _auth_headers(v, url)
    _image_size(v)

    portrait = H.image_arg() or H.test_portrait()

    # --- 1 + 2: does it serve, and did the model actually get baked into the image?
    health = requests.get(url + "/", headers=headers, timeout=300)
    if health.status_code != 200:
        v.verdict = H.NO_GO
        v.headline = (
            f"the container does not serve on Cloud Run (GET / -> {health.status_code}: "
            f"{health.text[:200]}). EMERGENCY: spec 03's face architecture is unbuildable "
            "as designed — escalate before writing any face code."
        )
        return

    info = health.json()
    v.evidence.append(H.save_bytes("face_health.json", json.dumps(info, indent=2).encode()))
    v.note(f"in-container model load at boot: {info.get('model_load_seconds')}s")
    v.note(f"model root: {info.get('model_root')}")
    v.note(
        "model found on disk BEFORE the load (i.e. genuinely baked, no runtime download): "
        f"{info.get('baked_at_build')}"
    )
    v.note(f"onnxruntime providers: {info.get('onnxruntime_providers')}")

    baked = bool(info.get("baked_at_build"))
    model_load = float(info.get("model_load_seconds") or 0.0)

    # --- 3a: per-photo cost, judged on the SERVER's own timings.
    portrait_bytes = portrait.read_bytes()
    first, first_ms = _embed(url, headers, portrait_bytes)
    if not first.get("faces"):
        v.verdict = H.NO_GO
        v.headline = (
            f"the service runs but detected 0 faces in {portrait.name} "
            f"({first.get('error') or first}). Detection is broken — escalate."
        )
        return

    latencies = [first_ms]
    server_ms = [first["decode_ms"] + first["inference_ms"]]
    for _ in range(WARM_REQUESTS - 1):
        result, elapsed_ms = _embed(url, headers, portrait_bytes)
        latencies.append(elapsed_ms)
        server_ms.append(result["decode_ms"] + result["inference_ms"])

    round_trip_median = statistics.median(latencies)
    server_median = statistics.median(server_ms)
    v.note(
        f"{WARM_REQUESTS} warm requests on a {first.get('image_shape')} image "
        f"({len(portrait_bytes) / 1024:.0f} KB): server-side decode+inference median "
        f"{server_median:.0f}ms — THIS is the number that transfers to the worker"
    )
    v.note(
        f"same requests, round-trip from this dev box: median {round_trip_median:.0f}ms "
        f"(min {min(latencies):.0f} / max {max(latencies):.0f}) — dominated by uploading "
        "the image over home broadband; the real worker reads from GCS in-region, so this "
        "number does NOT transfer to production"
    )

    # --- 3b: cold start, which is only observable on scale-out.
    burst_cold = _burst(url, headers, _small_jpeg(portrait, v), v)
    if burst_cold is not None:
        v.note(f"scale-out cold start (new instance boot + first embed): {burst_cold}s")
    cold_reference = burst_cold if burst_cold is not None else model_load

    # --- 4: is the vector what the Firestore index expects?
    face = first["results"][0]
    dim, norm = face["dim"], face["l2_norm"]
    v.note(
        f"embedding: dim={dim}, L2 norm={norm}, det_score={face['det_score']}, "
        f"faces detected={first['faces']}"
    )
    vector_ok = dim == EXPECTED_DIM and abs(norm - 1.0) < 1e-3
    if not vector_ok:
        v.note(
            f"MISMATCH: spec 03 indexes {EXPECTED_DIM}-d unit vectors so DOT_PRODUCT acts "
            "as cosine — this does not satisfy that"
        )

    # --- 4 bonus: same identity, fully restyled. The real matcher's hard case.
    similarity = None
    styled = H.artifact("cast_portrait_styled.png")
    if styled.exists() and portrait.name == H.PORTRAIT:
        styled_result, _ = _embed(url, headers, styled.read_bytes())
        if styled_result.get("faces"):
            similarity = round(
                _cosine(face["embedding"], styled_result["results"][0]["embedding"]), 4
            )
            v.note(
                f"same face, fully restyled: cosine similarity {similarity} over all {dim} dims "
                f"(spec-03 match floor is {SAME_IDENTITY_FLOOR}) — this is the "
                "'same guest, different lighting' case the matcher lives on"
            )
            if similarity < SAME_IDENTITY_FLOOR:
                v.note(
                    "below the floor: a restyle is harder than a lighting change, so this is "
                    "not fatal, but spec 03's threshold wants tuning against real event photos "
                    "in the eval harness rather than being taken from the ArcFace paper"
                )
        else:
            v.note("restyled portrait: 0 faces detected — worth a look during spec-03 tuning")

    # --- verdict
    v.cost_usd = 0.0
    v.note("cost: Cloud Run request+CPU time for ~8 requests is sub-cent; Cloud Build minutes are free-tier")

    problems = []
    if not baked:
        problems.append(
            "model NOT baked — the container downloads 326 MB from GitHub on every boot, "
            "which is both a cold-start tax and a third-party runtime dependency"
        )
    if cold_reference > COLD_START_CEILING_S:
        problems.append(f"cold start {cold_reference}s over the {COLD_START_CEILING_S}s ceiling")
    if server_median > SERVER_LATENCY_CEILING_MS:
        problems.append(
            f"server-side embed {server_median:.0f}ms over the {SERVER_LATENCY_CEILING_MS:.0f}ms ceiling"
        )
    if not vector_ok:
        problems.append(f"embedding is dim={dim} norm={norm}, not {EXPECTED_DIM}-d unit")

    if not vector_ok:
        v.verdict = H.NO_GO
        v.headline = (
            f"InsightFace serves on Cloud Run but the embedding contract is wrong ({'; '.join(problems)}). "
            "Spec 03's vector index assumptions need revisiting — escalate."
        )
    elif problems:
        v.verdict = H.PARTIAL
        v.headline = (
            f"InsightFace runs on Cloud Run with correct {EXPECTED_DIM}-d unit embeddings "
            f"({server_median:.0f}ms server-side per photo), but: {'; '.join(problems)}. "
            "Buildable as designed, with that caveat carried into spec 09 sizing."
        )
    else:
        v.verdict = H.GO
        v.headline = (
            f"buffalo_l baked into the image, loads in {model_load}s at boot, "
            f"{server_median:.0f}ms server-side per photo, cold start {cold_reference}s on "
            f"scale-out, {EXPECTED_DIM}-d unit-norm vectors on ONNX CPU"
            + (f", restyle cosine {similarity}" if similarity is not None else "")
            + ". Spec 03's face architecture and spec 09's worker-face sizing both hold — no replan."
        )

    v.note(
        f"tear down when done: gcloud run services delete {SERVICE} "
        f"--region {H.location()} --project {H.project()} --quiet"
    )


if __name__ == "__main__":
    H.run(
        "face_run",
        "Does InsightFace (buffalo_l on ONNX CPU) serve on Cloud Run with a baked model, "
        "acceptable cold start and latency, and 512-d unit-norm embeddings?",
        body,
        gate="NO-GO is the ONE real emergency in B1: spec 03 (face indexer, private albums, "
        "bounties, reel casting) and spec 09 (worker-face sizing) both assume this works. "
        "Escalate immediately rather than working around it.",
    )
