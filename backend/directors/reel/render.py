"""RENDER — spec 06 §3 step 5: the only place in the fleet that touches reel pixels.

Everything decided by then is already on the reel document, so this module has one job and no
judgement: fetch the assets, run the filtergraph `ffmpeg_build.py` produced, put the file in the
curated bucket. That separation is why the hard part is testable offline.

Two scope choices are deliberate and both narrow what this process can reach:

- **It reads the `display_1600` derived render, never an original.** `sa-render` therefore needs
  `objectViewer` on `derived` and `objectCreator` on `curated`, and no grant at all on `raw` — the same
  posture every perception worker has (deploy/buckets.sh). The cost is that a 1080×1920 frame is
  upscaled from a 1600 px source, which is why `REEL_ZOOM` is 1.25 and not 2.0. The alternative was
  handing the reel renderer the guests' full-resolution photographs, which is a much larger blast
  radius for a visibly better gradient.
- **It does not stream.** Assets land in a temp directory, ffmpeg reads local files, the result is
  uploaded once. A `-i https://…` filtergraph over 15 signed URLs would make a render's success depend
  on 15 network reads holding for three minutes.

`ffmpeg` writes progress to stderr; it is drained on a thread and translated into the reel document's
`progress` field so the client's bar is a real measurement rather than a timer.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass

from schemas.reel import ReelStatus, ShotDoc
from shared import gcs, log
from shared.settings import REEL_FPS, settings

from . import ffmpeg_build, store
from .select import Candidate

#: The render is bounded so a pathological filtergraph cannot hold an 8-vCPU job open indefinitely.
#: Spec 06 §3 puts a render at 2–5 minutes; 15 covers a cold job, 24 shots and a slow bucket.
RENDER_TIMEOUT_SEC = 900

#: Progress band this step owns. SELECT/DIRECT/CRITIC/SCORE report up to 45; publish takes it to 100.
PROGRESS_FLOOR = 45
PROGRESS_CEILING = 95


@dataclass
class Rendered:
    gcs_uri: str
    size_bytes: int
    duration: float


def _download(workdir: str, candidates: list[Candidate], shots: list[ShotDoc]) -> list[str]:
    """One local file per shot, in shot order. A repeat of the same mediaId is downloaded once."""
    by_id = {c.media_id: c for c in candidates}
    cache: dict[str, str] = {}
    paths: list[str] = []
    for index, shot in enumerate(shots):
        cached = cache.get(shot.mediaId)
        if cached is not None:
            paths.append(cached)
            continue
        candidate = by_id[shot.mediaId]
        parsed = gcs.parse_gs_uri(candidate.display_uri)
        if parsed is None:
            raise RuntimeError(f"{shot.mediaId}: unparseable render URI {candidate.display_uri!r}")
        bucket, path = parsed
        local = os.path.join(workdir, f"shot{index:02d}_{shot.mediaId}.jpg")
        with open(local, "wb") as handle:
            handle.write(gcs.download_bytes(bucket, path))
        cache[shot.mediaId] = local
        paths.append(local)
    return paths


def _pump_progress(process: subprocess.Popen, event_id: str, reel_id: str, total_frames: int) -> None:
    """Translate ffmpeg's `-progress` stream into the reel document. Best effort by design."""
    last = PROGRESS_FLOOR
    assert process.stdout is not None
    for line in process.stdout:
        if not line.startswith("frame="):
            continue
        try:
            frame = int(line.split("=", 1)[1].strip())
        except ValueError:
            continue
        pct = PROGRESS_FLOOR + int(
            (PROGRESS_CEILING - PROGRESS_FLOOR) * min(1.0, frame / max(1, total_frames))
        )
        # Only write on a visible move: a 30 fps render would otherwise write ~900 documents.
        if pct >= last + 5:
            last = pct
            try:
                store.progress(event_id, reel_id, pct)
            except Exception as exc:  # noqa: BLE001 - a progress bar must never fail a render
                log.debug("reel_progress_write_failed", err=str(exc))


def run(
    event_id: str,
    reel_id: str,
    *,
    shots: list[ShotDoc],
    candidates: list[Candidate],
    audio: bytes | None,
    fitted: list[bool],
) -> Rendered:
    """Build the file and upload it. Raises on failure; the caller turns that into `ops/` + `failed`."""
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg is not on PATH — this step only runs in the render image")

    by_id = {c.media_id: c for c in candidates}
    workdir = tempfile.mkdtemp(prefix=f"reel_{reel_id}_")
    try:
        image_paths = _download(workdir, candidates, shots)
        audio_path = None
        if audio:
            audio_path = os.path.join(workdir, "score.mp3")
            with open(audio_path, "wb") as handle:
                handle.write(audio)

        output = os.path.join(workdir, f"{reel_id}.mp4")
        plan = ffmpeg_build.build_command(
            shots,
            image_paths,
            sizes=[(by_id[s.mediaId].width, by_id[s.mediaId].height) for s in shots],
            fitted=fitted,
            audio_path=audio_path,
            output_path=output,
            captions=any(s.captionLine for s in shots),
        )

        captions_path = os.path.join(workdir, ffmpeg_build.CAPTIONS_FILENAME)
        with open(captions_path, "w", encoding="utf-8") as handle:
            handle.write(plan.ass)

        log.info(
            "reel_render_start",
            event_id=event_id,
            reel_id=reel_id,
            shots=len(shots),
            duration=plan.duration,
            audio=bool(audio),
        )
        # `-progress pipe:1` is a *global* option, so it goes before the inputs rather than after the
        # output — ffmpeg tolerates the wrong position for some options and not for this one.
        argv = plan.args[:1] + ["-progress", "pipe:1", "-nostats"] + plan.args[1:]
        # `cwd=workdir` so the filtergraph can name `captions.ass` without escaping a Windows-or-Linux
        # absolute path through ffmpeg's filter parser, where `:` and `\` are both syntax.
        process = subprocess.Popen(
            argv, cwd=workdir, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        # Deliberately not `communicate()`: it would read stdout itself, and the progress thread is
        # already draining that pipe — two readers on one pipe loses lines nondeterministically. So the
        # thread owns stdout, this thread owns stderr, and `wait` owns the exit code.
        pump = threading.Thread(
            target=_pump_progress,
            args=(process, event_id, reel_id, int(plan.duration * REEL_FPS)),
            daemon=True,
        )
        pump.start()
        stderr = process.stderr.read() if process.stderr else ""
        try:
            process.wait(timeout=RENDER_TIMEOUT_SEC)
        except subprocess.TimeoutExpired:
            process.kill()
            raise RuntimeError(f"ffmpeg exceeded {RENDER_TIMEOUT_SEC}s") from None
        pump.join(timeout=5)

        if process.returncode != 0:
            # ffmpeg's useful message is the last few lines, not the first.
            tail = "\n".join((stderr or "").strip().splitlines()[-8:])
            raise RuntimeError(f"ffmpeg exited {process.returncode}: {tail[:800]}")
        if not os.path.exists(output) or os.path.getsize(output) == 0:
            raise RuntimeError("ffmpeg exited cleanly but produced no file")

        size = os.path.getsize(output)
        with open(output, "rb") as handle:
            data = handle.read()

        cfg = settings()
        path = f"{event_id}/reels/{reel_id}.mp4"
        uri = gcs.upload_bytes(
            cfg.curated_bucket,
            path,
            data,
            content_type="video/mp4",
            # Immutable: a reel is never overwritten in place. A better cut is a new version with a new
            # id (spec 06 §4), which is also what makes the kiosk's atomic playlist swap possible.
            cache_control="public, max-age=31536000, immutable",
        )
        log.info(
            "reel_render_done",
            event_id=event_id,
            reel_id=reel_id,
            bytes=size,
            duration=plan.duration,
        )
        store.progress(event_id, reel_id, PROGRESS_CEILING, status=ReelStatus.RENDERING)
        return Rendered(gcs_uri=uri, size_bytes=size, duration=plan.duration)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def playable_url(event_id: str, reel_id: str) -> str:
    """The stable URL the kiosk's `<video src>` loads.

    Not a signed GCS URL: a `<video>` element cannot carry an Authorization header, and a signed URL
    stored in a Firestore document expires while the document still advertises it. So the document
    holds an `api` path that re-checks the reel's `visibility` on every request and 302s to a
    short-lived signed URL (`api/reels.py`). Unpublishing a reel therefore actually revokes access,
    rather than merely hiding a link that still works.
    """
    base = (settings().api_base_url or "").rstrip("/")
    return f"{base}/v1/events/{event_id}/reels/{reel_id}/video"
