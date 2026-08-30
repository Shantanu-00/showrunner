"""Single entrypoint for every service that shares the common image.

`gcloud run deploy --source` builds from a Dockerfile but cannot pass build args, so "which
service is this" cannot be baked in at build time. Instead the common image ships api/intake/dlq/
worker-curate/worker-safety/publisher together and the `SERVICE` env var — set per `gcloud run
deploy` — selects the app at import time.

Two services do **not** ride the common image, for the same reason and with the same consequence:

- `worker-face` bakes InsightFace's ~700 MB of native deps (`backend/docker/Dockerfile.face`);
- `worker-video-prep` bakes ffmpeg/ffprobe (`backend/docker/Dockerfile.video-prep`).

Both still route through this file, because both are uvicorn services — a separate image changes the
dependency set, not the dispatch. (`render` is the genuine exception: its Dockerfile ends with an
`ENTRYPOINT` and it is a Cloud Run Job, so it bypasses `main.py` entirely.)

The failure mode here is deliberate: an unrecognised `SERVICE` raises at import, which surfaces as a
Cloud Run revision that never becomes ready rather than as a runtime 500 on the first request. That is
what caught the old `make deploy-video-prep`, which built the ffmpeg-less common image.
"""

from __future__ import annotations

import os

SERVICE = os.environ.get("SERVICE", "api").strip().lower()

if SERVICE == "api":
    from api.app import app
elif SERVICE == "intake":
    from intake.app import app
elif SERVICE == "dlq":
    from workers.dlq.app import app
elif SERVICE == "worker-curate":
    from workers.curate.app import app
elif SERVICE == "worker-face":
    from workers.face.app import app
elif SERVICE == "worker-safety":
    from workers.safety.app import app
elif SERVICE == "worker-video-prep":
    from workers.video_prep.app import app
elif SERVICE == "publisher":
    from publisher.app import app
else:  # fail loudly at startup — a mis-set SERVICE must not silently serve the wrong surface
    raise RuntimeError(
        f"unknown SERVICE={SERVICE!r} (expected one of: api, intake, dlq, worker-curate, "
        "worker-face, worker-safety, worker-video-prep, publisher)"
    )

__all__ = ["app"]
