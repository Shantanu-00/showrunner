"""EDL — the deterministic half of the cut: beat-snapped timings and face-safe Ken Burns geometry.

Pure functions, no network, no Firestore. Two things are *proved* here rather than tested, and both are
spec 06 §8 acceptance criteria:

**1. Cuts land on beats, exactly.** Not "within ±80 ms" — every boundary this file emits *is* an
element of the beat grid librosa returned, so the error is zero by construction. The nominal durations
the director asked for are advisory: they are reshaped by the pacing curve, scaled to the music's
length, then snapped, and the min/max clamp is applied by moving to a *neighbouring beat* rather than
by an arbitrary offset. Nothing leaves the grid at any point, so nothing can drift back onto it.

**2. No face crosses the frame edge during any move.** Ken Burns is a linear interpolation between two
rectangles. For an axis-aligned rect, both `x(p) = x₀ + (x₁-x₀)p` and `x(p) + w(p)` are linear in `p`,
so each attains its extremes at `p = 0` and `p = 1`. If both endpoint rectangles contain the faces'
bounding box, every intermediate rectangle does too. So containment only has to hold at two rectangles
— which `_framing` guarantees by construction, reducing the zoom until it fits and switching to a
letterboxed `fit` composition when even the unzoomed 9:16 crop cannot hold the faces. A group photo is
never cropped through somebody's head to make an aspect ratio work; it is fitted, with its own blurred
copy behind it, and the *backdrop* takes the movement instead.

The model chose the gesture; this file does the arithmetic. That division is why the criterion is a
property of the code and not a hope about a prompt.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from schemas.reel import KenBurnsMove, PacingCurve, ShotDoc, ShotPlan, Transition
from shared.settings import (
    REEL_FACE_MARGIN,
    REEL_HEIGHT,
    REEL_MAX_SHOT_SEC,
    REEL_MIN_SHOT_SEC,
    REEL_TRANSITION_SEC,
    REEL_WIDTH,
    REEL_ZOOM,
)

from .select import Candidate

Rect = tuple[float, float, float, float]  # normalised (x, y, w, h) of the source image

TARGET_ASPECT = REEL_WIDTH / REEL_HEIGHT

#: Zoom held constant during a pan, so the move reads as a pan and not as a pan-plus-push.
PAN_ZOOM = 1.12


@dataclass(frozen=True)
class Framing:
    """How one photograph is put on a 1080×1920 canvas.

    `mode='crop'`: the visible region travels between `from_rect` and `to_rect` inside the source.
    `mode='fit'`: the whole photograph sits in frame untouched (nothing is cropped, therefore no face
    can leave it) and the animated element is the blurred backdrop behind it.
    """

    mode: Literal["crop", "fit"]
    from_rect: Rect
    to_rect: Rect


# ---------------------------------------------------------------- geometry


def _faces_bbox(candidate: Candidate) -> Rect | None:
    """Union of every face box, expanded by the safety margin. Normalised, clamped to the image."""
    if not candidate.face_boxes:
        return None
    x0 = min(b[0] for b in candidate.face_boxes)
    y0 = min(b[1] for b in candidate.face_boxes)
    x1 = max(b[0] + b[2] for b in candidate.face_boxes)
    y1 = max(b[1] + b[3] for b in candidate.face_boxes)
    m = REEL_FACE_MARGIN
    x0, y0 = max(0.0, x0 - m), max(0.0, y0 - m)
    x1, y1 = min(1.0, x1 + m), min(1.0, y1 + m)
    return (x0, y0, max(1e-6, x1 - x0), max(1e-6, y1 - y0))


def _crop_size(width: int, height: int, zoom: float) -> tuple[float, float]:
    """Normalised (w, h) of the visible region at `zoom`, at the output's 9:16 aspect.

    Done in normalised coordinates with the pixel aspect folded in, because every face box is
    normalised: a box survives a re-render at a different size, a pixel rectangle does not.
    """
    if width <= 0 or height <= 0:
        # No dimensions on the media doc — assume the render is already the output aspect. Safe: the
        # containment check below then either passes or falls through to `fit`.
        return (1.0 / zoom, 1.0 / zoom)
    source_aspect = width / height
    if source_aspect > TARGET_ASPECT:
        # Wider than 9:16: full height, cropped width.
        return (TARGET_ASPECT / source_aspect / zoom, 1.0 / zoom)
    return (1.0 / zoom, source_aspect / TARGET_ASPECT / zoom)


def _place(view_w: float, view_h: float, focus: Rect | None, shift: float = 0.0) -> Rect | None:
    """Centre a `view_w × view_h` window on `focus`, nudged by `shift` (fractions of the free slack).

    Returns None when the window cannot contain `focus` — the caller's signal to zoom out or to fit.
    """
    if view_w > 1.0 + 1e-6 or view_h > 1.0 + 1e-6:
        return None
    if focus is None:
        cx, cy = 0.5, 0.5
    else:
        fx, fy, fw, fh = focus
        if fw > view_w + 1e-6 or fh > view_h + 1e-6:
            return None
        cx, cy = fx + fw / 2, fy + fh / 2

    x = cx - view_w / 2
    y = cy - view_h / 2
    if shift:
        x += shift * (1.0 - view_w) / 2

    x = min(max(0.0, x), 1.0 - view_w)
    y = min(max(0.0, y), 1.0 - view_h)

    if focus is not None:
        fx, fy, fw, fh = focus
        # Pull back inside if centring plus the shift pushed a face out. This is the line that makes the
        # containment proof's premise true; everything after it is interpolation.
        x = min(max(x, fx + fw - view_w), fx)
        y = min(max(y, fy + fh - view_h), fy)
        x = min(max(0.0, x), 1.0 - view_w)
        y = min(max(0.0, y), 1.0 - view_h)
        if x > fx + 1e-6 or y > fy + 1e-6 or x + view_w < fx + fw - 1e-6 or y + view_h < fy + fh - 1e-6:
            return None
    return (round(x, 5), round(y, 5), round(view_w, 5), round(view_h, 5))


def framing(candidate: Candidate, move: KenBurnsMove) -> Framing:
    """The two endpoint rectangles for one shot. Never returns a framing that clips a face.

    Degrades in one direction only — towards showing more of the photograph: the requested zoom is
    reduced towards 1.0 while the faces still fit, and if they do not fit even then the shot becomes a
    `fit` composition. A move is a nicety; a face at the edge of the frame is the shot.
    """
    focus = _faces_bbox(candidate)

    if move is KenBurnsMove.PAN_LEFT:
        zooms, shifts = (PAN_ZOOM, PAN_ZOOM), (0.9, -0.9)
    elif move is KenBurnsMove.PAN_RIGHT:
        zooms, shifts = (PAN_ZOOM, PAN_ZOOM), (-0.9, 0.9)
    elif move is KenBurnsMove.PULL_OUT:
        zooms, shifts = (REEL_ZOOM, 1.0), (0.0, 0.0)
    elif move is KenBurnsMove.HOLD:
        zooms, shifts = (1.0, 1.0), (0.0, 0.0)
    else:  # PUSH_IN
        zooms, shifts = (1.0, REEL_ZOOM), (0.0, 0.0)

    # Walk the zoom back towards 1.0 until both endpoints contain the faces.
    for relax in (1.0, 0.75, 0.5, 0.25, 0.0):
        pair: list[Rect] = []
        for zoom, shift in zip(zooms, shifts):
            z = 1.0 + (zoom - 1.0) * relax
            view_w, view_h = _crop_size(candidate.width, candidate.height, z)
            rect = _place(view_w, view_h, focus, shift * relax)
            if rect is None:
                pair = []
                break
            pair.append(rect)
        if len(pair) == 2:
            return Framing("crop", pair[0], pair[1])

    # The faces are spread wider than any 9:16 crop of this photograph. Fit the whole frame.
    return Framing("fit", (0.0, 0.0, 1.0, 1.0), (0.0, 0.0, 1.0, 1.0))


def faces_inside(candidate: Candidate, frame: Framing) -> bool:
    """The linter's restatement of the proof: check the two endpoints, and the middle follows.

    Asserted by `scripts/smoke_reel.py --offline` over every shot of a real storyboard, which is spec
    06 §8's "linter test on EDL + face boxes" with no network and no spend.
    """
    if frame.mode == "fit":
        return True
    focus = _faces_bbox(candidate)
    if focus is None:
        return True
    fx, fy, fw, fh = focus
    for x, y, w, h in (frame.from_rect, frame.to_rect):
        if x > fx + 1e-4 or y > fy + 1e-4 or x + w < fx + fw - 1e-4 or y + h < fy + fh - 1e-4:
            return False
    return True


# ---------------------------------------------------------------- timing


#: Spec 06 §2.3's pacing curves, as a duration multiplier across the reel. Renormalised afterwards, so
#: a curve redistributes time without changing how long the reel is or overriding the director's own
#: relative emphasis — it shapes, it does not replace.
def _pacing_weights(count: int, curve: PacingCurve) -> list[float]:
    if count <= 1:
        return [1.0] * count
    out: list[float] = []
    for i in range(count):
        p = i / (count - 1)
        if curve is PacingCurve.LINEAR_BUILD:
            out.append(1.15 - 0.30 * p)  # shots shorten: acceleration
        elif curve is PacingCurve.PEAK_AND_SETTLE:
            out.append(0.85 + 0.30 * p)  # open fast, land long
        else:  # TWO_ACT — a held beat at the hinge, then a different energy
            hinge = abs(p - 0.5)
            out.append((1.25 if hinge < 0.08 else (1.05 if p < 0.5 else 0.9)))
    mean = sum(out) / len(out)
    return [w / mean for w in out]


def _nearest(value: float, grid: list[float]) -> float:
    return min(grid, key=lambda t: abs(t - value))


def _next_after(value: float, grid: list[float]) -> float | None:
    return next((t for t in grid if t > value + 1e-6), None)


@dataclass
class Edl:
    shots: list[ShotDoc]
    duration: float
    beat_error_ms: float
    notes: list[str]


def build(
    plan_shots: list[ShotPlan],
    candidates: list[Candidate],
    *,
    curve: PacingCurve,
    beats: list[float],
    downbeats: list[float],
    music_duration: float,
) -> Edl:
    """Turn a linted storyboard plus a beat grid into a renderable EDL.

    The order of operations matters and is the whole reason the beat criterion holds: reshape → scale
    to the music → snap → enforce the min/max by stepping along the grid. Snapping last would put every
    boundary on a beat and then the clamp would push some of them off again.
    """
    by_id = {c.media_id: c for c in candidates}
    notes: list[str] = []
    if not plan_shots:
        return Edl([], 0.0, 0.0, ["no shots"])

    usable_beats = [t for t in beats if t <= music_duration + 1e-6] or beats
    if len(usable_beats) < 2:
        notes.append("beat grid too short to cut against; falling back to nominal durations")
        usable_beats = []

    # --- nominal durations in seconds, from beats × the pacing curve.
    period = 60.0 / 120.0
    if len(usable_beats) >= 2:
        period = (usable_beats[-1] - usable_beats[0]) / (len(usable_beats) - 1)
    weights = _pacing_weights(len(plan_shots), curve)
    nominal = [
        max(0.2, shot.durationBeats * period * w) for shot, w in zip(plan_shots, weights)
    ]

    # --- scale to the available music, leaving a beat of air at the end.
    budget = max(4.0, (music_duration or sum(nominal)) - period)
    total = sum(nominal)
    if total > budget:
        scale = budget / total
        nominal = [d * scale for d in nominal]
        notes.append(f"storyboard ran {total:.1f}s against {budget:.1f}s of music — scaled {scale:.2f}×")

    # --- boundaries, snapped to the grid.
    boundaries: list[float] = [usable_beats[0] if usable_beats else 0.0]
    for index, duration in enumerate(nominal):
        target = boundaries[-1] + duration
        if usable_beats:
            grid = downbeats if _wants_downbeat(plan_shots, index) else usable_beats
            grid = [t for t in grid if t > boundaries[-1] + 1e-6] or [
                t for t in usable_beats if t > boundaries[-1] + 1e-6
            ]
            if not grid:
                break
            edge = _nearest(target, grid)
            # Enforce the min/max by stepping along the grid, never by an arbitrary offset — this is
            # what keeps every boundary an exact beat time.
            while edge - boundaries[-1] < REEL_MIN_SHOT_SEC:
                nxt = _next_after(edge, grid)
                if nxt is None:
                    break
                edge = nxt
            while edge - boundaries[-1] > REEL_MAX_SHOT_SEC:
                earlier = [t for t in grid if t < edge and t - boundaries[-1] >= REEL_MIN_SHOT_SEC]
                if not earlier:
                    break
                edge = earlier[-1]
        else:
            edge = boundaries[-1] + min(max(duration, REEL_MIN_SHOT_SEC), REEL_MAX_SHOT_SEC)
        if edge <= boundaries[-1]:
            break
        boundaries.append(round(edge, 4))

    shots: list[ShotDoc] = []
    for index in range(len(boundaries) - 1):
        plan = plan_shots[index]
        candidate = by_id.get(plan.mediaId)
        if candidate is None:  # linted upstream; belt and braces
            continue
        frame = framing(candidate, plan.move)
        # `cut` is the absence of a transition, so it carries no duration; every other value overlaps
        # the two clips by `REEL_TRANSITION_SEC`, which is why the input clip is built longer than the
        # timeline slot in `ffmpeg_build.py`.
        is_last = index == len(boundaries) - 2
        transition = plan.transition if not is_last else Transition.FADE
        shots.append(
            ShotDoc(
                mediaId=plan.mediaId,
                startSec=boundaries[index],
                endSec=boundaries[index + 1],
                fromRect=list(frame.from_rect),
                toRect=list(frame.to_rect),
                transition=transition,
                transitionSec=0.0 if transition is Transition.CUT else REEL_TRANSITION_SEC,
                captionLine=plan.captionLine,
                onBeat=boundaries[index],
            )
        )
        if frame.mode == "fit":
            notes.append(f"{plan.mediaId}: faces span wider than a 9:16 crop — fitted, not cropped")

    if len(shots) < len(plan_shots):
        notes.append(
            f"{len(plan_shots) - len(shots)} shots did not fit the music and were dropped from the tail"
        )

    error = 0.0
    if usable_beats:
        error = max(
            (min(abs(s.startSec - b) for b in usable_beats) for s in shots), default=0.0
        ) * 1000.0

    return Edl(
        shots=shots,
        duration=round(shots[-1].endSec - shots[0].startSec, 3) if shots else 0.0,
        beat_error_ms=round(error, 3),
        notes=notes,
    )


def _wants_downbeat(plan_shots: list[ShotPlan], index: int) -> bool:
    """A boundary lands on a downbeat when the shot *after* it is an emphasis shot — the emphasis is
    the arrival, so the downbeat belongs to its first frame, not to its last."""
    nxt = index + 1
    return nxt < len(plan_shots) and plan_shots[nxt].emphasis
