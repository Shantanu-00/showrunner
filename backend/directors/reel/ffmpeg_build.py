"""The filtergraph — spec 06 §3 step 5, as a pure function.

`build_command` takes an EDL and a list of local file paths and returns an ffmpeg argv plus an ASS
subtitle file. It touches no disk and no network, which is the point: the hardest part of a render is
the arithmetic, and arithmetic that only runs inside an 8-vCPU Cloud Run Job is arithmetic nobody
checks. `scripts/smoke_reel.py --offline` asserts the offsets and the geometry with no ffmpeg present.

Three pieces of it are non-obvious enough to state:

**1. `zoompan` crops at the *source's* aspect ratio, not the output's.** Its visible region is
`(iw/z, ih/z)`, so a 9:16 window over a 4:3 photograph is not expressible in it. So each shot is
pre-cropped to the smallest 9:16 rectangle containing both endpoint rectangles (`_base_crop`), and the
Ken Burns move then happens *inside* a source that is already 9:16 — where `zoompan` is exact. The two
endpoint rectangles are re-expressed relative to that base crop, which is a change of coordinates and
not a change of geometry, so `edl.py`'s containment proof survives it intact.

**2. Every transition is an `xfade`, including a cut.** A cut is rendered as a one-frame dissolve
(33 ms at 30 fps — perceptually a cut). Mixing `concat` for cuts with `xfade` for everything else would
mean two different offset arithmetics in one chain, and the offsets are the thing that has to be right.
With one uniform chain the offset of the i-th crossfade is exactly the EDL's (i+1)-th boundary — the
derivation is in `_xfade_chain` — so the cut points on screen are the beat times librosa reported, with
no accumulated rounding.

**3. Captions are burned in via `ass`, not `drawtext`.** Not for shaping (they are Latin-script only by
policy — HANDOFF §3) but because one subtitle file with real timings is far less error-prone than N
`drawtext` filters carrying `enable='between(t,..)'` expressions, and it is the same file libass would
use on the documented production path.

**4. Every shot chain ends in an explicit `fps` filter, on top of `zoompan`'s own `fps=` parameter.**
`xfade` refuses a variable frame rate ("the inputs needs to be a constant frame rate"), and the first
real render (never exercised before this Job actually ran once) hit exactly that on a live audio+video
graph that the offline smoke fixture — no ffmpeg, no real frame rate negotiation — could not catch:
`zoompan`'s frame-rate metadata is not reliably what a downstream `xfade` reads back once `trim` and
`format` sit between them, so the chain re-stamps a hard CFR itself instead of trusting it survived.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from schemas.reel import ShotDoc, Transition
from shared.settings import (
    REEL_FPS,
    REEL_HEIGHT,
    REEL_TRANSITION_SEC,
    REEL_WIDTH,
)

#: Cap on the intermediate upscale. A 1600 px derived render pushed past this is only inventing pixels,
#: and every extra one costs render seconds on a job billed by the vCPU-second.
MAX_INTERMEDIATE_PX = 3240

#: A cut, expressed as the shortest crossfade the frame rate allows.
CUT_SEC = round(1.0 / REEL_FPS, 4)

CAPTIONS_FILENAME = "captions.ass"

#: Backdrop movement for a `fit` shot. The foreground is static by construction — that is what makes a
#: fitted shot uncroppable — so the drift lives behind it.
FIT_BACKDROP_ZOOM = 1.10


@dataclass(frozen=True)
class Plan:
    """A rendered plan: what to run, what the captions are, how long the result will be."""

    args: list[str]
    ass: str
    duration: float
    input_durations: list[float]
    offsets: list[float]


# ---------------------------------------------------------------- geometry helpers


def _base_crop(shot: ShotDoc, width: int, height: int) -> tuple[float, float, float, float]:
    """The smallest 9:16 rectangle (normalised, inside the image) containing both endpoint rects."""
    fx, fy, fw, fh = shot.fromRect
    tx, ty, tw, th = shot.toRect
    x0, y0 = min(fx, tx), min(fy, ty)
    x1, y1 = max(fx + fw, tx + tw), max(fy + fh, ty + th)

    if width <= 0 or height <= 0:
        return (0.0, 0.0, 1.0, 1.0)

    target = REEL_WIDTH / REEL_HEIGHT
    # Work in pixels, where the aspect ratio means what it says.
    px, py = x0 * width, y0 * height
    pw, ph = max(1.0, (x1 - x0) * width), max(1.0, (y1 - y0) * height)
    if pw / ph > target:
        ph = pw / target
    else:
        pw = ph * target
    # Re-centre, then clamp; if it does not fit, shrink to fit and keep the aspect.
    cx, cy = px + (x1 - x0) * width / 2, py + (y1 - y0) * height / 2
    if pw > width:
        pw, ph = float(width), width / target
    if ph > height:
        ph, pw = float(height), height * target
    px = min(max(0.0, cx - pw / 2), max(0.0, width - pw))
    py = min(max(0.0, cy - ph / 2), max(0.0, height - ph))
    return (px / width, py / height, pw / width, ph / height)


def _relative(rect: list[float], base: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    """Re-express a normalised source rect in the base crop's coordinates."""
    bx, by, bw, bh = base
    x, y, w, h = rect
    return (
        min(max(0.0, (x - bx) / bw), 1.0),
        min(max(0.0, (y - by) / bh), 1.0),
        min(max(1e-4, w / bw), 1.0),
        min(max(1e-4, h / bh), 1.0),
    )


def _even(value: float) -> int:
    """H.264 wants even dimensions; `scale`'s `-2` handles one axis, this handles the other."""
    return max(2, int(value) // 2 * 2)


# ---------------------------------------------------------------- per-shot chains


def _shot_chain(
    index: int,
    shot: ShotDoc,
    *,
    width: int,
    height: int,
    input_duration: float,
    fitted: bool,
) -> str:
    frames = max(2, int(round(input_duration * REEL_FPS)))
    base = _base_crop(shot, width, height)

    if fitted:
        # Nothing is cropped: the whole photograph is letterboxed over a blurred, slowly drifting copy
        # of itself. The only shot type where a face physically cannot leave the frame.
        return (
            f"[{index}:v]split=2[bg{index}][fg{index}];"
            f"[bg{index}]scale={REEL_WIDTH * 2}:{REEL_HEIGHT * 2}:force_original_aspect_ratio=increase,"
            f"crop={REEL_WIDTH * 2}:{REEL_HEIGHT * 2},"
            f"zoompan=z='1+{FIT_BACKDROP_ZOOM - 1:.4f}*on/{frames - 1}':x='iw/2-(iw/zoom/2)':"
            f"y='ih/2-(ih/zoom/2)':d=1:s={REEL_WIDTH}x{REEL_HEIGHT}:fps={REEL_FPS},"
            f"gblur=sigma=28,eq=brightness=-0.16[bgv{index}];"
            f"[fg{index}]scale={REEL_WIDTH}:{REEL_HEIGHT}:force_original_aspect_ratio=decrease[fgv{index}];"
            f"[bgv{index}][fgv{index}]overlay=(W-w)/2:(H-h)/2,"
            f"trim=duration={input_duration:.4f},setpts=PTS-STARTPTS,"
            f"format=yuv420p,setsar=1,fps={REEL_FPS}[v{index}]"
        )

    fx, fy, fw, _fh = _relative(shot.fromRect, base)
    tx, ty, tw, _th = _relative(shot.toRect, base)

    # Upscale so even the tightest visible window still fills 1080 px wide, capped.
    tightest = max(1e-3, min(fw, tw))
    intermediate = _even(min(MAX_INTERMEDIATE_PX, math.ceil(REEL_WIDTH / tightest)))

    crop = (
        f"crop=iw*{base[2]:.5f}:ih*{base[3]:.5f}:iw*{base[0]:.5f}:ih*{base[1]:.5f}"
    )
    # zoompan: visible width = iw/z, and we want iw*w(p) ⇒ z = 1/w(p). x and y are the visible
    # region's top-left in input pixels. `on` is the output frame index, so p = on/(frames-1).
    zoom = f"1/({fw:.5f}+({tw - fw:+.5f})*on/{frames - 1})"
    xexpr = f"({fx:.5f}+({tx - fx:+.5f})*on/{frames - 1})*iw"
    yexpr = f"({fy:.5f}+({ty - fy:+.5f})*on/{frames - 1})*ih"
    return (
        f"[{index}:v]{crop},scale={intermediate}:-2,"
        f"zoompan=z='{zoom}':x='{xexpr}':y='{yexpr}':d=1:"
        f"s={REEL_WIDTH}x{REEL_HEIGHT}:fps={REEL_FPS},"
        f"trim=duration={input_duration:.4f},setpts=PTS-STARTPTS,"
        f"format=yuv420p,setsar=1,fps={REEL_FPS}[v{index}]"
    )


def _transition_seconds(shot: ShotDoc) -> float:
    if shot.transition is Transition.CUT:
        return CUT_SEC
    return max(CUT_SEC, shot.transitionSec or REEL_TRANSITION_SEC)


def _xfade_name(shot: ShotDoc) -> str:
    return "fade" if shot.transition is Transition.CUT else shot.transition.value


def _xfade_chain(shots: list[ShotDoc]) -> tuple[str, list[float], list[float], float]:
    """The crossfade chain, plus each input clip's length and each crossfade's offset.

    Derivation, because getting it wrong is silent — the reel just drifts:

        clip i is built `slot_i + t_i` long, where `slot_i = end_i - start_i` and `t_i` is the
        transition it lends to the next clip (`t_last = 0`).
        after joining clip i, the accumulated stream is `Σ_{k≤i} slot_k + t_i` long.
        xfade's `offset` is where the crossfade *begins* in the accumulated stream, i.e.
        `accumulated − t_i = Σ_{k≤i} slot_k` — exactly the EDL's (i+1)-th cut point.

    So the offsets are the beat times, not an accumulation of them, and rounding cannot compound.
    """
    slots = [s.endSec - s.startSec for s in shots]
    trans = [_transition_seconds(s) for s in shots[:-1]] + [0.0]
    inputs = [slot + t for slot, t in zip(slots, trans)]

    offsets: list[float] = []
    parts: list[str] = []
    label = "v0"
    cumulative = 0.0
    for i in range(len(shots) - 1):
        cumulative += slots[i]
        offsets.append(round(cumulative, 4))
        out = f"x{i}"
        parts.append(
            f"[{label}][v{i + 1}]xfade=transition={_xfade_name(shots[i])}:"
            f"duration={trans[i]:.4f}:offset={cumulative:.4f}[{out}]"
        )
        label = out

    total = sum(slots)
    return (";".join(parts), inputs, offsets, round(total, 4))


# ---------------------------------------------------------------- captions


def _ass_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{int(hours)}:{int(minutes):02d}:{secs:05.2f}"


def build_ass(shots: list[ShotDoc], *, origin: float) -> str:
    """One ASS file for the whole reel.

    The styling is spec 12's typography in the only form ffmpeg understands: a heavy sans, gold
    (`&H00C39A4E` — ASS colours are `&HAABBGGRR`, so the channel order is reversed from CSS), a real
    shadow so it survives a bright photograph, bottom-anchored above the safe area. Deliberately no
    outline box: a caption on a wedding reel should read as a title card, not as a subtitle track.
    """
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {REEL_WIDTH}\n"
        f"PlayResY: {REEL_HEIGHT}\n"
        "WrapStyle: 2\n"
        "ScaledBorderAndShadow: yes\n"
        "\n[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Caption,DejaVu Sans,64,&H00C39A4E,&H00C39A4E,&H90000000,&H90000000,"
        "1,0,0,0,100,100,2,0,1,0,4,2,80,80,220,1\n"
        "\n[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    lines: list[str] = []
    for shot in shots:
        if not shot.captionLine:
            continue
        # Held slightly inside the shot so a caption never straddles a cut.
        start = shot.startSec - origin + 0.20
        end = max(start + 0.6, shot.endSec - origin - 0.20)
        text = shot.captionLine.replace("\n", " ").replace("{", "(").replace("}", ")")
        lines.append(
            f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Caption,,0,0,0,,"
            f"{{\\fad(220,220)}}{text}"
        )
    return header + "\n".join(lines) + "\n"


# ---------------------------------------------------------------- the command


def build_command(
    shots: list[ShotDoc],
    image_paths: list[str],
    *,
    sizes: list[tuple[int, int]],
    fitted: list[bool],
    audio_path: str | None,
    output_path: str,
    captions: bool,
) -> Plan:
    """Assemble the whole ffmpeg invocation. Pure — returns argv, never runs anything.

    `sizes` and `fitted` are parallel to `shots`: the source pixel dimensions (needed to turn a
    normalised rectangle into a 9:16 crop) and whether `edl.py` chose the fitted composition.
    """
    if not shots:
        raise ValueError("no shots to render")
    if not (len(shots) == len(image_paths) == len(sizes) == len(fitted)):
        raise ValueError("shots, paths, sizes and fitted must be parallel")

    chain, inputs, offsets, total = _xfade_chain(shots)
    origin = shots[0].startSec

    args: list[str] = ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-loglevel", "warning"]
    for path, duration in zip(image_paths, inputs):
        args += ["-loop", "1", "-framerate", str(REEL_FPS), "-t", f"{duration:.4f}", "-i", path]
    if audio_path:
        args += ["-i", audio_path]

    parts = [
        _shot_chain(
            i,
            shot,
            width=sizes[i][0],
            height=sizes[i][1],
            input_duration=inputs[i],
            fitted=fitted[i],
        )
        for i, shot in enumerate(shots)
    ]
    video_label = "v0" if len(shots) == 1 else f"x{len(shots) - 2}"
    if chain:
        parts.append(chain)

    tail = f"[{video_label}]"
    post = ["fade=t=in:st=0:d=0.4", f"fade=t=out:st={max(0.0, total - 0.6):.4f}:d=0.6"]
    if captions:
        post.append(f"ass={CAPTIONS_FILENAME}")
    parts.append(f"{tail}{','.join(post)}[vout]")

    maps = ["-map", "[vout]"]
    if audio_path:
        audio_index = len(shots)
        parts.append(
            f"[{audio_index}:a]atrim=duration={total:.4f},asetpts=PTS-STARTPTS,"
            f"afade=t=in:st=0:d=0.5,afade=t=out:st={max(0.0, total - 1.2):.4f}:d=1.2[aout]"
        )
        maps += ["-map", "[aout]", "-c:a", "aac", "-b:a", "128k"]

    args += ["-filter_complex", ";".join(parts)] + maps
    args += [
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "20",
        "-pix_fmt", "yuv420p",
        # `+faststart` moves the moov atom to the front, which is what lets the kiosk's <video> begin
        # playing a reel it is still downloading. Without it a premiere stalls until the whole file
        # lands, on a TV, in front of everybody.
        "-movflags", "+faststart",
        "-r", str(REEL_FPS),
        "-t", f"{total:.4f}",
        output_path,
    ]
    return Plan(args=args, ass=build_ass(shots, origin=origin), duration=total,
                input_durations=inputs, offsets=offsets)
