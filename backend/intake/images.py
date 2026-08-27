"""Photo decode, EXIF, GPS removal and the three derived renders.

Everything here is CPU-bound and runs inline in the intake request (Pillow is fast enough that
a task hop would cost more than it saves — spec 03 §4). Two rules hold throughout:

- **A decode failure is permanent.** A corrupt or masquerading file cannot be fixed by
  retrying, so the caller rejects it once and never re-enqueues (spec 03 §6).
- **Derived renders carry no metadata.** They are re-encoded WebP written from pixel data, so
  no EXIF (and therefore no GPS) can survive into the surfaces guests actually see.
"""

from __future__ import annotations

import datetime as dt
import io

import piexif
from PIL import Image, ImageOps, UnidentifiedImageError

from shared import log
from shared.settings import CLASSIFY_PX, DISPLAY_PX, THUMB_PX

# iPhone HEIC is the single most common guest upload format; without this it does not decode.
try:  # pragma: no cover - import-time capability probe
    import pillow_heif

    pillow_heif.register_heif_opener()
    HEIF_SUPPORTED = True
except Exception as exc:  # noqa: BLE001
    HEIF_SUPPORTED = False
    log.warn("heif_unavailable", err=str(exc))

Image.MAX_IMAGE_PIXELS = 200_000_000  # decompression-bomb guard, generous for phone panoramas

EXIF_DATETIME_ORIGINAL = 36867  # 0x9003
EXIF_OFFSET = 34665  # 0x8769 — the sub-IFD that actually holds DateTimeOriginal


class DecodeError(Exception):
    """Permanent: the bytes are not a decodable image."""


class Render:
    __slots__ = ("name", "data", "content_type", "width", "height")

    def __init__(self, name: str, data: bytes, content_type: str, width: int, height: int) -> None:
        self.name = name
        self.data = data
        self.content_type = content_type
        self.width = width
        self.height = height


def open_image(data: bytes) -> Image.Image:
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise DecodeError(str(exc)) from exc
    return img


def read_capture_time(data: bytes) -> dt.datetime | None:
    """EXIF `DateTimeOriginal` as a **naive** datetime.

    Naive on purpose: EXIF carries no timezone, so the caller interprets it in
    `event.timezone` (spec 03 §5.1). Returning an arbitrarily-localised value here would be the
    exact bug that makes a Haldi photo classify as a Sangeet.
    """
    try:
        exif = Image.open(io.BytesIO(data)).getexif()
    except Exception:  # noqa: BLE001 - EXIF absence is normal (WhatsApp forwards, screenshots)
        return None
    if not exif:
        return None
    raw = None
    try:
        sub = exif.get_ifd(EXIF_OFFSET)
        raw = sub.get(EXIF_DATETIME_ORIGINAL) if sub else None
    except Exception:  # noqa: BLE001
        raw = None
    raw = raw or exif.get(306)  # DateTime (modify time) as a weaker fallback
    if not isinstance(raw, str):
        return None
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return dt.datetime.strptime(raw.strip().split(".")[0], fmt)
        except ValueError:
            continue
    return None


def strip_gps(data: bytes, content_type: str) -> bytes | None:
    """Remove the GPS IFD from a JPEG **losslessly**; returns None if there was nothing to do.

    piexif edits the metadata block and leaves the compressed image data untouched, so the
    original a reel later trims from is bit-identical in pixels. Only JPEG has this path:
    HEIC/PNG/WebP would need a re-encode, which we refuse to do to originals — for those, GPS
    stays in the raw bytes (a private bucket) and is never read into Firestore, never present in
    a derived render, and never exposed on any surface. Disclosed in the README.
    """
    if content_type not in ("image/jpeg", "image/jpg"):
        return None
    try:
        exif = piexif.load(data)
    except Exception as exc:  # noqa: BLE001 - unparseable EXIF is not a reason to reject a photo
        log.warn("exif_load_failed", err=str(exc))
        return None
    if not exif.get("GPS"):
        return None
    exif["GPS"] = {}
    try:
        out = io.BytesIO()
        piexif.insert(piexif.dump(exif), data, out)
        return out.getvalue()
    except Exception as exc:  # noqa: BLE001
        log.warn("gps_strip_failed", err=str(exc))
        return None


def render_variants(img: Image.Image) -> tuple[list[Render], int, int]:
    """thumb_384 (grid) · classify_768 (what Gemini sees) · display_1600 (lightbox/kiosk).

    Sizes are spec 01 §4; classify_768 exists so a Gemini call costs a predictable 258–1548
    tokens instead of whatever the phone happened to shoot.
    """
    base = ImageOps.exif_transpose(img)  # honour orientation, then discard the tag
    if base.mode not in ("RGB", "L"):
        base = base.convert("RGB")
    width, height = base.size

    renders: list[Render] = []
    for name, px, quality in (
        (f"thumb_{THUMB_PX}.webp", THUMB_PX, 80),
        (f"classify_{CLASSIFY_PX}.webp", CLASSIFY_PX, 85),
        (f"display_{DISPLAY_PX}.webp", DISPLAY_PX, 88),
    ):
        variant = base.copy()
        variant.thumbnail((px, px), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        variant.save(buf, format="WEBP", quality=quality, method=4)
        renders.append(
            Render(name, buf.getvalue(), "image/webp", variant.width, variant.height)
        )
    return renders, width, height
