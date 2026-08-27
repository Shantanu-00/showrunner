"""Minimal InsightFace service — the B1 probe for spec 03's Face Indexer worker.

Endpoints:
  GET  /        readiness + how long the baked model took to load (the cold-start number)
  POST /embed   raw image bytes in -> per-face 512-d unit-norm embeddings + timings

Deliberately NOT the real worker: no Firestore, no vector search, no Cloud Tasks ack
semantics. It answers "does this container work on Cloud Run and how fast is it?" and
nothing else. The real worker lives at backend/workers/face.
"""

from __future__ import annotations

import os
import time
import uuid

import numpy as np
from fastapi import FastAPI, Request

# Import-time model load: identical to what the real worker does, so the number we
# measure here IS the worker's cold start.
#
# `root` must match the Dockerfile's bake path exactly. insightface silently falls back to
# downloading 326 MB from GitHub if it can't find the model there, which would turn a
# missing-model bug into a slow-but-working service — the worst possible failure mode.
MODEL_ROOT = os.environ.get("MODEL_ROOT", "/models")

# Answer "was the model baked?" by looking BEFORE the load. Checking afterwards always says
# yes: insightface silently creates the directory and downloads into it, so a post-hoc check
# reports success for the exact failure we're trying to detect.
BAKED_AT_BUILD = os.path.isdir(os.path.join(MODEL_ROOT, "models", "buffalo_l"))

# Distinguishes instances, so the probe can tell a scale-out cold start from a warm hit.
INSTANCE_ID = uuid.uuid4().hex[:8]

_load_started = time.monotonic()
import insightface  # noqa: E402

_analyzer = insightface.app.FaceAnalysis(
    name="buffalo_l", root=MODEL_ROOT, providers=["CPUExecutionProvider"]
)
_analyzer.prepare(ctx_id=-1, det_size=(640, 640))
MODEL_LOAD_SECONDS = round(time.monotonic() - _load_started, 2)

app = FastAPI()


@app.get("/")
def health() -> dict:
    return {
        "ready": True,
        "model": "buffalo_l",
        "model_load_seconds": MODEL_LOAD_SECONDS,
        "model_root": MODEL_ROOT,
        "baked_at_build": BAKED_AT_BUILD,
        "instance_id": INSTANCE_ID,
        "onnxruntime_providers": _analyzer.models["recognition"].session.get_providers(),
    }


@app.post("/embed")
async def embed(request: Request) -> dict:
    raw = await request.body()

    decode_started = time.monotonic()
    import cv2

    image = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        return {"error": "could not decode image", "bytes": len(raw)}
    decode_ms = round((time.monotonic() - decode_started) * 1000, 1)

    infer_started = time.monotonic()
    faces = _analyzer.get(image)
    infer_ms = round((time.monotonic() - infer_started) * 1000, 1)

    out = []
    for face in faces:
        # spec 03: L2-normalize before it ever reaches Firestore, so DOT_PRODUCT on the
        # vector index behaves as cosine similarity.
        vector = face.normed_embedding.astype(float)
        out.append(
            {
                "bbox": [round(float(x), 1) for x in face.bbox],
                "det_score": round(float(face.det_score), 4),
                "dim": int(vector.shape[0]),
                "l2_norm": round(float(np.linalg.norm(vector)), 6),
                # Full vector, not a head: the probe compares two photos of the same face,
                # and a partial dot product over 8 of 512 dims answers nothing.
                "embedding": [round(float(x), 6) for x in vector],
            }
        )

    return {
        "image_shape": list(image.shape),
        "faces": len(out),
        "bytes_received": len(raw),
        "decode_ms": decode_ms,
        "inference_ms": infer_ms,
        "model_load_seconds": MODEL_LOAD_SECONDS,
        "baked_at_build": BAKED_AT_BUILD,
        "instance_id": INSTANCE_ID,
        "results": out,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
