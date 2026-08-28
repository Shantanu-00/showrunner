"""OPENER — spec 06 §5: once per event, an 8 s Veo 3.1 Fast image-to-video clip from the top
couple portrait, prepended to the `couple` reel's final cut (bonus +0.2, HANDOFF §6).

Two failure-tolerant properties, the same discipline `music.py` already carries for Lyria:

- **A generation failure degrades to no opener, never a missing reel.** The `couple` reel publishes
  without it and `events/{eventId}.openerFailed` records why, so a persistently-failing event does
  not retry an $0.80 spend on every later commission.
- **Generated once per event, not once per version.** Spec 06 §5 says "once per event" — the clip is
  cached at `events/{eventId}.openerUri` (a curated-bucket object) and every later `couple` version
  reuses it. `pipeline.py` calls `ensure()` on every commission; only the first ever actually pays.

Veo's Vertex publisher ID differs from the AI Studio ID `.env.example` pins for `MODEL_VIDEO_GEN`
(HANDOFF §9, decided 2026-08-28): `MODEL_VIDEO_GEN_VERTEX` is the second key this module reads, and
the render job always calls the enterprise/Vertex path (`enterprise=True`) — the same posture as
every other model call any worker in this fleet makes (spec 09's guest-media-on-billed-tier rule).
"""

from __future__ import annotations

import functools
import time
from dataclasses import dataclass

from shared import fs, gcs, log
from shared.settings import REEL_OPENER_COST_PER_SECOND_USD, REEL_OPENER_SECONDS, settings

from .select import Candidate

#: Veo is regional on Vertex — a `global` call 404s (risk probe, `scripts/risk_tests/veo.py`).
VEO_LOCATION = "us-central1"

#: A generation running this long is not coming back; declared a permanent failure so the event's
#: `openerFailed` guard stops a later commission from waiting on it again.
POLL_TIMEOUT_SECONDS = 300

PROMPT = (
    "Slow cinematic push-in on the couple as the string lights behind them drift gently out of "
    "focus. A half-smile settles. Handheld warmth, shallow depth of field, golden evening light. "
    "No cuts, no dialogue, no on-screen text."
)


@dataclass
class Opener:
    """What `ensure()` returns. `cost_usd` is nonzero only on the commission that actually paid for
    generation — a cache hit or a remembered failure both carry `0.0`, so `pipeline.py` can add it
    to the reel's cost line without ever double-counting the spend."""

    video: bytes | None
    cost_usd: float = 0.0
    failure: str | None = None

    @property
    def ok(self) -> bool:
        return self.video is not None


def top_couple_portrait(candidates: list[Candidate]) -> Candidate | None:
    """The reel's own SELECT already restricts `couple` to tier ≤1 people (`select.py`); this picks
    the best single-subject shot of a principal to animate — a crowd shot is not a portrait, and
    Veo's `person_generation=allow_adult` policy is calibrated against a photo of one or two people,
    not a wedding party line-up."""
    principals = [c for c in candidates if c.top_tier == 0 and (c.people_count or 0) <= 2]
    pool = principals or [c for c in candidates if c.top_tier <= 1 and (c.people_count or 0) <= 2]
    pool = pool or candidates
    return max(pool, key=lambda c: c.aesthetic, default=None)


@functools.lru_cache(maxsize=1)
def _client():
    from google import genai

    cfg = settings()
    return genai.Client(enterprise=True, project=cfg.project, location=VEO_LOCATION)


def _model_candidates() -> list[str]:
    """Env value first, then the known-reachable Vertex IDs — same ladder shape as the risk probe,
    so a future model-ID rename degrades to "try the next one" rather than a hard 404."""
    cfg = settings()
    ids = [cfg.model_video_gen_vertex, "veo-3.1-fast-generate-001", "veo-3.1-generate-001"]
    return list(dict.fromkeys(i for i in ids if i))


def _generate(portrait: bytes, mime: str) -> tuple[bytes | None, str, str | None]:
    """One attempt across the candidate model-ID ladder. Never raises — returns
    `(video_bytes | None, model_id, error_message)`."""
    from google.genai import types

    client = _client()
    last_error = ""
    for model in _model_candidates():
        try:
            operation = client.models.generate_videos(
                model=model,
                source=types.GenerateVideosSource(
                    prompt=PROMPT, image=types.Image(image_bytes=portrait, mime_type=mime)
                ),
                config=types.GenerateVideosConfig(
                    duration_seconds=REEL_OPENER_SECONDS,
                    aspect_ratio="9:16",
                    resolution="720p",
                    person_generation="allow_adult",
                    # Silent on purpose: the reel's own Lyria track (`music.py`) owns the audio, and it
                    # starts exactly where the main render's clip begins. An audible Veo track would
                    # compete with it for the opener's 8 seconds before the music has even started.
                    generate_audio=False,
                    number_of_videos=1,
                ),
            )
        except Exception as exc:  # noqa: BLE001 - try the next candidate ID
            last_error = f"{model}: {type(exc).__name__}: {exc}"[:200]
            continue

        started = time.monotonic()
        timed_out = False
        while not operation.done:
            if time.monotonic() - started > POLL_TIMEOUT_SECONDS:
                timed_out = True
                break
            time.sleep(10)
            operation = client.operations.get(operation)
        if timed_out:
            last_error = f"{model}: operation still running after {POLL_TIMEOUT_SECONDS}s"
            continue
        if operation.error:
            last_error = f"{model}: {operation.error}"
            continue

        videos = getattr(operation.response, "generated_videos", None) or []
        if not videos:
            last_error = f"{model}: operation completed with no video"
            continue
        raw = getattr(videos[0].video, "video_bytes", None)
        if raw:
            return raw, model, None
        last_error = f"{model}: delivery was a URI, not inline bytes"

    return None, "", last_error or "no reachable Veo endpoint"


def ensure(event_id: str, candidates: list[Candidate]) -> Opener:
    """The cached, once-per-event opener: reuse a cached clip, remember a permanent failure, or
    generate and cache a fresh one. Never raises — every branch returns an `Opener`."""
    event = fs.get_event(event_id) or {}

    cached_uri = event.get("openerUri")
    if cached_uri:
        parsed = gcs.parse_gs_uri(cached_uri)
        if parsed is not None:
            try:
                return Opener(video=gcs.download_bytes(*parsed))
            except Exception as exc:  # noqa: BLE001 - a cache-read failure regenerates, not fails
                log.warn("opener_cache_read_failed", event_id=event_id, err=str(exc))

    if event.get("openerFailed"):
        # Already tried and failed for this event; do not spend another $0.80 on every commission.
        return Opener(video=None, failure=str(event["openerFailed"]))

    portrait = top_couple_portrait(candidates)
    if portrait is None:
        return Opener(video=None, failure="no eligible couple portrait to animate")

    source = gcs.parse_gs_uri(portrait.display_uri)
    if source is None:
        return Opener(video=None, failure=f"unparseable portrait URI {portrait.display_uri!r}")
    try:
        image_bytes = gcs.download_bytes(*source)
    except Exception as exc:  # noqa: BLE001
        return Opener(video=None, failure=f"could not fetch portrait: {exc}"[:300])

    mime = "image/png" if portrait.display_uri.lower().endswith(".png") else "image/jpeg"
    video, model, error = _generate(image_bytes, mime)
    if not video:
        fs.event_ref(event_id).update({"openerFailed": (error or "unknown error")[:300]})
        log.warn("opener_generation_failed", event_id=event_id, err=error)
        return Opener(video=None, failure=error)

    cost = REEL_OPENER_SECONDS * REEL_OPENER_COST_PER_SECOND_USD
    uri = gcs.upload_bytes(
        settings().curated_bucket,
        f"{event_id}/reels/opener.mp4",
        video,
        content_type="video/mp4",
        cache_control="public, max-age=31536000, immutable",
    )
    fs.event_ref(event_id).update({"openerUri": uri, "openerCostUsd": cost, "openerModel": model})
    log.info("opener_generated", event_id=event_id, model=model, cost_usd=cost)
    return Opener(video=video, cost_usd=cost)
