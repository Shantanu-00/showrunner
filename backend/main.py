"""Single entrypoint for every service in this image.

`gcloud run deploy --source` builds from a Dockerfile but cannot pass build args, so "which
service is this" cannot be baked in at build time. Instead one image ships all of them and the
`SERVICE` env var — set per `gcloud run deploy` — selects the app at import time. Three services,
one build, one dependency set to keep in sync.
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
else:  # fail loudly at startup — a mis-set SERVICE must not silently serve the wrong surface
    raise RuntimeError(f"unknown SERVICE={SERVICE!r} (expected one of: api, intake, dlq)")

__all__ = ["app"]
