"""Single entrypoint for every service that shares the common image.

`gcloud run deploy --source` builds from a Dockerfile but cannot pass build args, so "which
service is this" cannot be baked in at build time. Instead the common image ships api/intake/dlq/
worker-curate together and the `SERVICE` env var — set per `gcloud run deploy` — selects the app
at import time. `worker-face` is the exception: InsightFace's ~700 MB of native deps would bloat
every other service's cold start, so it gets its own image from `backend/docker/Dockerfile.face`
(same `main.py`, same dispatch, just built with a different dependency set baked in).
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
elif SERVICE == "publisher":
    from publisher.app import app
else:  # fail loudly at startup — a mis-set SERVICE must not silently serve the wrong surface
    raise RuntimeError(
        f"unknown SERVICE={SERVICE!r} (expected one of: api, intake, dlq, worker-curate, "
        "worker-face, worker-safety, publisher)"
    )

__all__ = ["app"]
