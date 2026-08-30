"""The ffmpeg/ffprobe half of `video-prep` — every subprocess this worker runs, and nothing else.

Kept apart from `app.py` for the same reason `directors/reel/ffmpeg_build.py` is kept apart from
`render.py`: the handler's job is the order of operations and the failure taxonomy, and neither is
readable with argv lists interleaved through it. Everything here takes and returns bytes or plain
values, so `scripts/smoke_upload.py --video` can exercise the shapes and a local machine with ffmpeg
on PATH can run the whole module against a real file.

Three conventions inherited from `directors/reel/render.py`, all of them learned the hard way:

- **Nothing streams.** ffmpeg reads a local file and writes local files; the caller does the GCS
  round trips. A pipe would save a temp file and cost the ability to seek, which `-ss` needs.
- **A non-zero exit is read from the *tail* of stderr**, because ffmpeg's useful message is the last
  few lines, and the full stderr is logged separately when a run fails.
- **Timeouts kill the process rather than waiting on it.** An ffmpeg that has stopped making progress
  will sit there until Cloud Run kills the container, which turns one bad clip into a stalled queue.

The one decision worth arguing about is the poster: spec 03 §4 says "best of 3 sampled frames by
sharpness", and sharpness here is variance-of-Laplacian, computed with Pillow rather than OpenCV
(`FIND_EDGES` then variance). It is the standard cheap proxy for focus, and it is deliberately not a
model call — picking a thumbnail is not a judgment, and a clip whose poster needed an LLM would be a
clip whose poster cost more than its classification.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
from dataclasses import dataclass

from PIL import Image, ImageFilter, ImageOps

from shared.settings import (
    FFMPEG_TIMEOUT_SEC,
    KEYFRAME_FPS,
    MAX_KEYFRAMES,
    POSTER_CANDIDATES,
    PROXY_HEIGHT,
)


class ProbeError(Exception):
    """The file is not a video we can read. Permanent — the same bytes fail the same way forever."""


class TranscodeError(Exception):
    """ffmpeg ran and failed. The caller decides whether that is permanent (see `app.py`)."""


@dataclass(frozen=True)
class Probe:
    """What `ffprobe` could tell us about the container. Everything else is derived from these."""

    duration_sec: float
    width: int
    height: int
    codec: str
    has_audio: bool
    #: The container's `creation_time`, ISO-8601 as ffprobe reports it, or "". Unlike photo EXIF this
    #: is normally offset-aware, so `app.py` parses rather than localising it.
    creation_time: str


def require_ffmpeg() -> None:
    """Fail loudly at the top of the handler rather than three steps in.

    `Dockerfile.video-prep` asserts both binaries at build time, so reaching this in production means
    the wrong image is deployed under `SERVICE=worker-video-prep` — which is exactly the mistake the
    old `--source backend` Makefile target used to make silently.
    """
    for binary in ("ffmpeg", "ffprobe"):
        if shutil.which(binary) is None:
            raise RuntimeError(
                f"{binary} is not on PATH — worker-video-prep must run the "
                "backend/docker/Dockerfile.video-prep image, not the common one"
            )


def _run(argv: list[str], *, what: str, timeout: int = FFMPEG_TIMEOUT_SEC) -> str:
    """Run one subprocess to completion; return stderr. Raises `TranscodeError` on failure."""
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired:
        raise TranscodeError(f"{what} exceeded {timeout}s") from None
    if proc.returncode != 0:
        tail = "\n".join((proc.stderr or "").strip().splitlines()[-6:])
        raise TranscodeError(f"{what} exited {proc.returncode}: {tail[:600]}")
    return proc.stderr or ""


# ---------------------------------------------------------------- probe


def probe(path: str) -> Probe:
    """`ffprobe` the container. Anything unparseable is a `ProbeError`, i.e. permanent."""
    argv = [
        "ffprobe",
        "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        path,
    ]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=60, check=False)
    except subprocess.TimeoutExpired:
        raise ProbeError("ffprobe timed out") from None
    if proc.returncode != 0:
        tail = "\n".join((proc.stderr or "").strip().splitlines()[-4:])
        raise ProbeError(f"ffprobe exited {proc.returncode}: {tail[:400]}")

    try:
        meta = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise ProbeError(f"ffprobe returned unparseable JSON: {exc}") from exc

    streams = meta.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        # A `.mp4` extension over an audio-only or non-media payload. The signed Content-Type made
        # this unlikely, which is exactly why it is suspicious rather than routine.
        raise ProbeError("no video stream in this file")

    fmt = meta.get("format") or {}
    duration = _as_float(fmt.get("duration")) or _as_float(video.get("duration")) or 0.0
    if duration <= 0.0:
        raise ProbeError("could not determine duration")

    # `width`/`height` are pre-rotation. A phone shooting vertically reports 1920x1080 with a
    # rotate tag, and using the raw pair would pick landscape sizing for a portrait clip; ffmpeg's
    # own scale filter honours the tag, so the *rendered* frames are what the poster measures and
    # these values exist only for the record.
    return Probe(
        duration_sec=round(duration, 3),
        width=int(video.get("width") or 0),
        height=int(video.get("height") or 0),
        codec=str(video.get("codec_name") or "unknown"),
        has_audio=any(s.get("codec_type") == "audio" for s in streams),
        creation_time=str((fmt.get("tags") or {}).get("creation_time") or ""),
    )


def _as_float(value: object) -> float:
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return 0.0


# ---------------------------------------------------------------- frames


def _extract_frame(src: str, workdir: str, at_sec: float, name: str) -> bytes:
    """One PNG frame at `at_sec`. `-ss` before `-i` so ffmpeg seeks instead of decoding forward."""
    out = os.path.join(workdir, name)
    _run(
        [
            "ffmpeg", "-hide_banner", "-nostdin", "-y",
            "-ss", f"{max(0.0, at_sec):.3f}",
            "-i", src,
            "-frames:v", "1",
            "-f", "image2",
            out,
        ],
        what=f"frame at {at_sec:.1f}s",
        timeout=60,
    )
    with open(out, "rb") as handle:
        return handle.read()


def sharpness(data: bytes) -> float:
    """Variance of the edge response — the standard cheap focus proxy, via Pillow not OpenCV.

    Downscaled first so the number means the same thing on a 4K clip and a 720p one: edge variance
    scales with resolution, and comparing three frames of the *same* clip would still work, but a
    stable absolute scale makes the logged value worth reading.
    """
    try:
        with Image.open(io.BytesIO(data)) as img:
            grey = ImageOps.grayscale(img)
            grey.thumbnail((512, 512), Image.Resampling.BILINEAR)
            edges = grey.filter(ImageFilter.FIND_EDGES)
            histogram = edges.histogram()
            total = sum(histogram) or 1
            mean = sum(i * n for i, n in enumerate(histogram)) / total
            return sum(n * (i - mean) ** 2 for i, n in enumerate(histogram)) / total
    except Exception:  # noqa: BLE001 - an unreadable frame simply loses the sharpness contest
        return 0.0


def poster_frame(src: str, workdir: str, duration_sec: float) -> bytes:
    """Spec 03 §4's "best of 3 sampled frames by sharpness".

    Sampled at 25/50/75% rather than 0/50/100%: the first and last frames of a phone clip are the
    ones most likely to be a blurred pan as the camera comes up or drops, so including them would
    reliably waste a third of the budget. Falls back to whichever frame did decode — a clip where two
    of three samples fail is still a clip we can represent.
    """
    candidates: list[tuple[float, bytes]] = []
    for index in range(POSTER_CANDIDATES):
        fraction = (index + 1) / (POSTER_CANDIDATES + 1)
        try:
            data = _extract_frame(src, workdir, duration_sec * fraction, f"poster_{index}.png")
        except TranscodeError:
            continue
        candidates.append((sharpness(data), data))

    if not candidates:
        raise TranscodeError("could not extract any frame for the poster")
    candidates.sort(key=lambda pair: pair[0], reverse=True)
    return candidates[0][1]


def keyframes(src: str, workdir: str, duration_sec: float) -> list[bytes]:
    """1 fps, capped at `MAX_KEYFRAMES`, in time order (spec 03 §4).

    One ffmpeg invocation with an `fps` filter rather than N seeks: on a long clip the seeks dominate,
    and the cap means a 5-minute video is sampled across its whole length instead of only its first
    twelve seconds — which would make the Guardian's "verdict applies to the whole clip" a claim about
    the opening shot.
    """
    pattern = os.path.join(workdir, "kf_%02d.webp")
    # `fps=1/N` where N spreads MAX_KEYFRAMES evenly across the duration once the clip is longer than
    # the cap; below that, plain 1 fps as the spec states.
    if duration_sec > MAX_KEYFRAMES / KEYFRAME_FPS:
        rate = f"{MAX_KEYFRAMES}/{duration_sec:.3f}"
    else:
        rate = str(KEYFRAME_FPS)

    _run(
        [
            "ffmpeg", "-hide_banner", "-nostdin", "-y",
            "-i", src,
            "-vf", f"fps={rate},scale='min(768,iw)':-2",
            "-frames:v", str(MAX_KEYFRAMES),
            "-c:v", "libwebp",
            "-quality", "82",
            pattern,
        ],
        what="keyframe extraction",
    )

    out: list[bytes] = []
    for index in range(1, MAX_KEYFRAMES + 1):
        path = os.path.join(workdir, f"kf_{index:02d}.webp")
        if not os.path.exists(path):
            break
        with open(path, "rb") as handle:
            out.append(handle.read())
    if not out:
        raise TranscodeError("keyframe extraction produced no files")
    return out


# ---------------------------------------------------------------- proxy


def proxy(src: str, workdir: str) -> bytes:
    """`proxy_720.mp4` — H.264, faststart, for in-app playback (spec 03 §4).

    `-movflags +faststart` is the whole point: without it the moov atom lands at the end of the file
    and a phone cannot begin playing until the last byte arrives, which on a venue's wifi is
    indistinguishable from the video being broken. Scaled to height 720 with `-2` width so the encoder
    always gets even dimensions, which H.264 requires and which an odd source height would otherwise
    violate.
    """
    out = os.path.join(workdir, "proxy_720.mp4")
    _run(
        [
            "ffmpeg", "-hide_banner", "-nostdin", "-y",
            "-i", src,
            "-vf", f"scale=-2:'min({PROXY_HEIGHT},ih)'",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "26",
            "-pix_fmt", "yuv420p",
            # Audio is carried through so the clip is watchable, and is *not* screened anywhere in
            # this build (nothing in the pipeline hears anything). `hasAudio` on the media document
            # records that, so the gap is visible rather than implied.
            "-c:a", "aac",
            "-b:a", "128k",
            "-movflags", "+faststart",
            out,
        ],
        what="proxy transcode",
    )
    with open(out, "rb") as handle:
        return handle.read()
