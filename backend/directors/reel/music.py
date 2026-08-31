"""SCORE — spec 06 §3 step 4: Lyria 3 writes the soundtrack, librosa finds the grid to cut on.

The call shape is the part worth reading, because none of it is in the SDK's type hints and all of it
was found the expensive way in the B1 risk probe (`scripts/risk_tests/lyria.py`, verdict GO):

- `location` **must** be `global`. `us-central1`/`us`/`eu` are rejected outright.
- `stream=True` is **mandatory**; the non-streaming call returns "Request contains an invalid
  argument".
- Passing `response_modalities` or `response_format` is **rejected** ("Audio delivery mode is not
  supported"). Send neither.
- There are no typed events for this surface — everything arrives as an unknown SSE event, so the
  `event.raw` dicts are read directly. Audio is base64 `audio/mpeg` in the `content.delta` whose
  `delta.type == "audio"`, and Lyria also emits a free prose caption of the piece, which is kept as
  reel provenance rather than shown to anyone.

The **beat grid is where the anti-generic claim becomes measurable**. The director asked for a tempo;
Lyria produced whatever it produced; librosa reports what is actually in the file. Cutting to the
requested tempo instead of the detected one is how a reel ends up drifting a beat behind its own music
by the end — so the request is a *brief* and the returned audio is the *truth*, and `edl.py` quantizes
to the detected grid.

**Failure is not fatal to the reel.** A Lyria outage means a silent reel, not a missing one: the
pipeline records `musicFailed`, synthesises a metronome grid at the brief's tempo so the cuts still
have a rhythm, and publishes. Losing the soundtrack costs a bonus point; losing the reel costs the
demo. (Transient errors get one retry first — a 429 on a $0.04 call is worth waiting out.)
"""

from __future__ import annotations

import base64
import functools
import os
import tempfile
import time
from dataclasses import dataclass, field

from schemas.reel import MusicBrief
from shared import log
from shared.settings import REEL_LYRIA_COST_USD, settings

#: Lyria 3 ignores the enterprise regional endpoints entirely (risk probe, 2026-08-27).
LYRIA_LOCATION = "global"

#: Assumed metre for downbeat placement. Lyria is not asked for a time signature and does not report
#: one, so "every fourth beat is a downbeat, counted from the first detected beat" is an assumption,
#: stated here rather than buried: it is right for the 4/4 celebration music every persona's brief asks
#: for, and when it is wrong the emphasis shots land on a beat that is merely not *the* beat — which is
#: inaudible, unlike a cut that lands between beats.
BEATS_PER_BAR = 4

#: Fallback grid when Lyria is unavailable, in seconds of silence to lay under the cut.
FALLBACK_DURATION_SEC = 32.0


class MusicError(RuntimeError):
    """Raised only after the retry. The caller degrades to a silent reel rather than failing."""


@dataclass
class Score:
    """What the render needs to cut on. `audio` is None for the silent fallback."""

    audio: bytes | None
    mime: str = "audio/mpeg"
    caption: str = ""
    tempo: float = 0.0
    beats: list[float] = field(default_factory=list)
    downbeats: list[float] = field(default_factory=list)
    duration: float = 0.0
    cost_usd: float = 0.0
    failure: str | None = None

    @property
    def silent(self) -> bool:
        return self.audio is None


def prompt_for(brief: MusicBrief, *, seed: int) -> str:
    """The Lyria prompt, assembled from the director's brief rather than from a template.

    `No vocals` is not stylistic: a vocal line competes with the burned-in captions, and the reel is
    watched on a wall with the sound coming from a room's PA.
    """
    parts = [
        f"Instrumental {brief.style or 'celebration'} score for a 30 second event film.",
        f"Tempo around {brief.tempoBpm} BPM with a clean, editable pulse.",
    ]
    if brief.instruments:
        parts.append("Instrumentation: " + ", ".join(brief.instruments) + ".")
    if brief.culturalRefs:
        parts.append("In the tradition of: " + ", ".join(brief.culturalRefs) + ".")
    if brief.arc:
        parts.append(f"Emotional arc: {brief.arc}.")
    parts.append(f"No vocals. Variation seed {seed % 10000}.")
    return " ".join(parts)


@functools.lru_cache(maxsize=1)
def _client():
    from google import genai

    cfg = settings()
    # `enterprise=True` is the 2026 replacement for `vertexai=True` — the billed first-party path,
    # which is the only path any of this project's media touches (see services/gemini.py).
    return genai.Client(enterprise=True, project=cfg.project, location=LYRIA_LOCATION)


def _stream(prompt: str) -> tuple[bytes, str, str]:
    chunks: list[bytes] = []
    mime, caption = "", ""
    stream = _client().interactions.create(
        model=settings().model_music, input=prompt, stream=True
    )
    for event in stream:
        raw = getattr(event, "raw", None)
        if not isinstance(raw, dict):
            continue
        kind = raw.get("event_type")
        if kind == "content.delta":
            delta = raw.get("delta") or {}
            if delta.get("type") == "audio" and delta.get("data"):
                mime = delta.get("mime_type", "") or mime
                chunks.append(base64.b64decode(delta["data"]))
            elif delta.get("type") == "text":
                caption += delta.get("text", "")
        elif kind == "error":
            raise MusicError(f"Lyria stream error: {raw.get('error')}")
    return b"".join(chunks), mime or "audio/mpeg", caption.strip()


def compose(brief: MusicBrief, *, seed: int, prompt: str | None = None) -> Score:
    """Generate the clip and analyse it. Never raises — a failure comes back as a silent `Score`.

    `prompt` overrides `prompt_for`, for the one other thing in this system that needs Lyria and is not
    a reel: the wall's ambient bed (`publisher/ambience.py`). A film score and a room's background music
    want opposite things from the same model — the reel prompt asks for a 30-second arc that builds to a
    peak, which is precisely what must *not* happen to music playing under photographs for an hour. The
    retry, the silent fallback and the beat analysis are identical either way, so only the sentence
    describing the music is parameterised.
    """
    prompt = prompt if prompt is not None else prompt_for(brief, seed=seed)
    audio, mime, caption = b"", "audio/mpeg", ""
    failure: str | None = None

    for attempt in (1, 2):
        try:
            audio, mime, caption = _stream(prompt)
            if audio:
                failure = None
                break
            failure = "Lyria stream completed but carried no audio payload"
        except Exception as exc:  # noqa: BLE001 - one retry, then degrade; classified by message only
            failure = f"{type(exc).__name__}: {exc}"[:300]
            log.warn("lyria_failed", attempt=attempt, err=failure)
        if attempt == 1:
            time.sleep(3)

    if not audio:
        log.warn("lyria_degraded_to_silence", err=failure)
        return _silent(brief, failure or "Lyria returned no audio")

    grid = beat_grid(audio)
    if not grid[1]:
        # The file exists but librosa found no pulse — keep the audio, use the requested tempo's grid.
        log.warn("beat_track_empty", bytes=len(audio))
        synthetic = _metronome(brief.tempoBpm, grid[2] or FALLBACK_DURATION_SEC)
        return Score(
            audio=audio,
            mime=mime,
            caption=caption,
            tempo=float(brief.tempoBpm),
            beats=synthetic,
            downbeats=synthetic[::BEATS_PER_BAR],
            duration=grid[2] or FALLBACK_DURATION_SEC,
            cost_usd=REEL_LYRIA_COST_USD,
            failure="no beat grid detected; cut to the requested tempo",
        )

    tempo, beats, duration = grid
    return Score(
        audio=audio,
        mime=mime,
        caption=caption,
        tempo=tempo,
        beats=beats,
        downbeats=beats[::BEATS_PER_BAR],
        duration=duration,
        cost_usd=REEL_LYRIA_COST_USD,
    )


def _silent(brief: MusicBrief, failure: str) -> Score:
    beats = _metronome(brief.tempoBpm, FALLBACK_DURATION_SEC)
    return Score(
        audio=None,
        caption="",
        tempo=float(brief.tempoBpm),
        beats=beats,
        downbeats=beats[::BEATS_PER_BAR],
        duration=FALLBACK_DURATION_SEC,
        cost_usd=0.0,
        failure=failure,
    )


def _metronome(bpm: int, duration: float) -> list[float]:
    """A synthetic grid at `bpm`. Pure — also what `smoke_reel.py --offline` cuts against."""
    step = 60.0 / max(1, min(300, int(bpm)))
    count = int(duration / step) + 1
    return [round(i * step, 4) for i in range(count)]


def beat_grid(audio: bytes) -> tuple[float, list[float], float]:
    """`(tempo_bpm, beat_times_sec, duration_sec)` from MP3 bytes, via librosa.

    Written to a temp file rather than handed to librosa as a buffer: MP3 decoding goes through
    `soundfile`/`audioread` depending on the libsndfile build, and the file path is the one input every
    backend accepts. `librosa<1.0` is pinned in `requirements-render.txt` because the `beat_track`
    return shape changed in 1.x (spec's global conventions).
    """
    try:
        import librosa
        import numpy as np
    except ImportError:  # pragma: no cover - only the render image carries librosa
        log.warn("librosa_unavailable")
        return 0.0, [], 0.0

    path = ""
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as handle:
            handle.write(audio)
            path = handle.name
        y, sr = librosa.load(path, sr=22050, mono=True)
        duration = float(librosa.get_duration(y=y, sr=sr))
        tempo, frames = librosa.beat.beat_track(y=y, sr=sr)
        # `librosa<1.0`'s beat_track returns tempo as a shape-(1,) ndarray, not a scalar — NumPy 2.x
        # dropped the implicit array→float conversion `float(tempo)` relied on for that shape, which
        # is exactly what turned a real Lyria render's tempo detection into a silent fallback the
        # first time this path ran against real audio rather than the offline smoke fixture.
        tempo_value = float(np.asarray(tempo).reshape(-1)[0])
        beats = [round(float(t), 4) for t in librosa.frames_to_time(frames, sr=sr)]
        return tempo_value, beats, duration
    except Exception as exc:  # noqa: BLE001 - a beat grid we cannot compute is a fallback, not a fault
        log.warn("beat_track_failed", err=str(exc)[:200])
        return 0.0, [], 0.0
    finally:
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass
