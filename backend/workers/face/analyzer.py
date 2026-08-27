"""InsightFace `buffalo_l` — import-time model load, per-photo detect+embed.

Import-time load is deliberate (spec 09 §1): the model is baked into the image at build
(`backend/docker/Dockerfile.face`), and loading it once at process start rather than per-request
is the entire reason `worker-face` runs at `min-instances=1` instead of eating a ~12 s cold start
on every cold request. `MODEL_ROOT` must match the Dockerfile's bake path exactly — insightface
resolves the model directory from the `root=` kwarg only, never from an env var, so a mismatch
here means a silent 326 MB re-download at boot rather than a clean failure.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import cv2
import numpy as np

from schemas.common import BoundingBox
from shared import log
from shared.settings import settings

_load_started = time.monotonic()
import insightface  # noqa: E402

_analyzer = insightface.app.FaceAnalysis(
    name="buffalo_l", root=settings().face_model_root, providers=["CPUExecutionProvider"]
)
_analyzer.prepare(ctx_id=-1, det_size=(640, 640))
MODEL_LOAD_SECONDS = round(time.monotonic() - _load_started, 2)
log.info("face_model_loaded", seconds=MODEL_LOAD_SECONDS, root=settings().face_model_root)


class DecodeError(Exception):
    """Permanent: the bytes are not a decodable image — retrying buys nothing."""


@dataclass(frozen=True)
class Detection:
    box: BoundingBox
    embedding: list[float]  # 512-d, unit-norm
    detScore: float


def _normalized_box(bbox: np.ndarray, width: int, height: int) -> BoundingBox:
    x0, y0, x1, y1 = [float(v) for v in bbox]
    x0, x1 = sorted((x0, x1))
    y0, y1 = sorted((y0, y1))
    # Clip before normalizing: InsightFace boxes can run a few px past the frame edge, and
    # BoundingBox requires w,h > 0 — a face flush against the border must not fail validation.
    x0, x1 = max(0.0, min(x0, width)), max(0.0, min(x1, width))
    y0, y1 = max(0.0, min(y0, height)), max(0.0, min(y1, height))
    w, h = max(x1 - x0, 1.0), max(y1 - y0, 1.0)
    return BoundingBox(
        x=x0 / width,
        y=y0 / height,
        w=min(w / width, 1.0),
        h=min(h / height, 1.0),
    )


def detect(image_bytes: bytes) -> list[Detection]:
    """Decode → detect → 5-pt align → embed → L2-normalize, largest face first.

    Sorted by box area (not detector score) because ranking by size is what the caller's
    `MAX_FACES_PER_MEDIA` truncation is for: in a 60-person baraat shot, the faces worth a face
    doc are the ones the photo is *about*, not whichever the detector happened to score highest.
    """
    buf = np.frombuffer(image_bytes, dtype=np.uint8)
    image = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    if image is None:
        raise DecodeError(f"cv2 could not decode {len(image_bytes)} bytes")
    height, width = image.shape[:2]

    faces = _analyzer.get(image)
    out: list[Detection] = []
    for face in faces:
        vector = face.normed_embedding.astype(float)
        out.append(
            Detection(
                box=_normalized_box(face.bbox, width, height),
                embedding=[round(float(v), 6) for v in vector],
                detScore=round(float(face.det_score), 4),
            )
        )
    out.sort(key=lambda d: d.box.w * d.box.h, reverse=True)
    return out
