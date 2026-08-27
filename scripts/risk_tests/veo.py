"""Probe: can we generate a Veo 3.1 Fast image-to-video clip from a guest portrait?

Spec 06: once per event, an 8 s cinematic opener is generated from the top couple
portrait and prepended to the `couple` reel (bonus +0.2). Two risks, one probe:
  1. Does the call work at all on the billed Vertex path, and does it return bytes we can
     feed straight into the ffmpeg render (vs a GCS-only delivery we'd have to plumb)?
  2. Does `person_generation` policy allow image-to-video from a photo of a person?
     Research says image-to-video is `allow_adult`-only and locked in EU/UK/CH/MENA —
     we're us-central1, but the API is the authority, not the docs.

The probe generates 4 s (~$0.40) rather than the production 8 s: same policy surface,
half the spend. Latency is recorded because it decides whether the opener can be
generated during the event or must be pre-warmed before the premiere.

Discovered call shape (2026-08-27):
  * `.env.example`'s `MODEL_VIDEO_GEN=veo-3.1-fast-generate-preview` is the **Gemini
    API / AI Studio** model ID. On the Vertex/GEAP path it 404s ("Publisher model ...
    not found") in every location. The Vertex publisher ID is
    **`veo-3.1-fast-generate-001`** — exactly as `docs/research/` recorded ("Vertex GA
    IDs veo-3.1-generate-001/-fast-"). We do not edit `.env.example` from a probe; the
    config question is escalated in HANDOFF §9. This probe walks a candidate ladder and
    reports which ID the project can actually reach.
  * Veo is regional: `us-central1` works, `global` 404s (the opposite of Lyria and
    Nano Banana, which are `global`-only).
  * The `prompt=`/`image=` kwargs are deprecated in google-genai 2.20.0 — pass
    `source=types.GenerateVideosSource(...)`.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _harness as H

PROBE_SECONDS = 4  # production opener is 8 s (spec 06); 4 s answers the same questions
COST_PER_SECOND = 0.10  # veo-3.1-fast

# Tried in order; first ID the project can reach wins. Env value first so that if
# .env.example is ever reconciled (HANDOFF §9), the probe follows it without an edit.
MODEL_CANDIDATES = ("veo-3.1-fast-generate-001", "veo-3.1-generate-001")

# Veo is regional on Vertex — global 404s.
VEO_LOCATION = "us-central1"

PROMPT = (
    "Slow cinematic push-in on the woman as the string lights behind her drift gently out "
    "of focus. She turns her head a few degrees toward camera and her half-smile settles. "
    "Handheld warmth, shallow depth of field, golden evening light. No cuts."
)


def _poll(client, operation, v: H.Verdict, timeout_s: int = 600):
    started = time.monotonic()
    while not operation.done:
        if time.monotonic() - started > timeout_s:
            raise TimeoutError(f"Veo operation still running after {timeout_s}s")
        time.sleep(10)
        operation = client.operations.get(operation)
        v.note(f"polling… {time.monotonic() - started:.0f}s elapsed")
    return operation


def _start(v: H.Verdict, client, model: str, image_bytes: bytes, mime: str):
    from google.genai import types

    return client.models.generate_videos(
        model=model,
        source=types.GenerateVideosSource(
            prompt=PROMPT,
            image=types.Image(image_bytes=image_bytes, mime_type=mime),
        ),
        config=types.GenerateVideosConfig(
            duration_seconds=PROBE_SECONDS,
            aspect_ratio="9:16",
            resolution="720p",
            person_generation="allow_adult",
            generate_audio=True,
            number_of_videos=1,
        ),
    )


def body(v: H.Verdict) -> None:
    source = H.image_arg() or H.test_portrait()
    mime = "image/jpeg" if source.suffix.lower() in {".jpg", ".jpeg"} else "image/png"
    image_bytes = source.read_bytes()
    v.note(f"source image: {source.name} ({len(image_bytes) / 1024:.0f} KB, {mime})")

    client = H.enterprise_client(VEO_LOCATION)
    candidates = [H.env("MODEL_VIDEO_GEN"), *MODEL_CANDIDATES]

    last_error = None
    for model in dict.fromkeys(candidates):  # dedupe, preserve order
        try:
            operation = _start(v, client, model, image_bytes, mime)
        except Exception as exc:
            last_error = exc
            v.note(f"{model}: unreachable — {type(exc).__name__}: {str(exc)[:200]}")
            continue

        loc = VEO_LOCATION
        v.note(f"{model} @ {loc}: operation accepted, polling…")
        operation = _poll(client, operation, v)
        if operation.error:
            last_error = RuntimeError(str(operation.error))
            v.note(f"{model}: operation failed — {operation.error}")
            continue

        v.cost_usd = PROBE_SECONDS * COST_PER_SECOND
        v.note(f"reachable Vertex model ID: {model}")
        videos = getattr(operation.response, "generated_videos", None) or []
        if not videos:
            v.verdict = H.NO_GO
            v.headline = f"{loc}: operation completed but returned no video: {operation.response}"
            return

        video = videos[0].video
        raw = getattr(video, "video_bytes", None)
        uri = getattr(video, "uri", None)
        v.note(f"{loc}: returned mime={video.mime_type} inline_bytes={bool(raw)} uri={uri}")

        if raw:
            v.evidence.append(H.save_bytes("veo_opener.mp4", raw))
            delivery = "inline bytes — drops straight into the ffmpeg render, no GCS plumbing"
        else:
            delivery = f"GCS/URI delivery only ({uri}) — render job must fetch it first"
            v.note("no inline bytes: wire output_gcs_uri in spec 06's opener step")

        v.verdict = H.GO
        v.headline = (
            f"{PROBE_SECONDS}s 9:16 720p image-to-video from a photo of a person via "
            f"`{model}` @ {loc}, person_generation=allow_adult accepted, ${v.cost_usd:.2f}. "
            f"{delivery}. +0.2 Veo bonus is live."
        )
        return

    v.verdict = H.NO_GO
    v.headline = f"no reachable Veo endpoint — last error: {last_error}"


if __name__ == "__main__":
    H.run(
        "veo",
        "Can Veo 3.1 Fast turn a guest portrait into a cinematic opener clip?",
        body,
        gate="NO-GO = the couple reel loses its opener and the +0.2 Veo bonus. "
        "Reel itself is unaffected; swap the bonus to another model only if a "
        "cheap alternative exists.",
    )
