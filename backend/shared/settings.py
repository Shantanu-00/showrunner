"""Runtime configuration for every Showrunner service.

Deliberately dependency-light (no pydantic-settings): services import this at module
import time on Cloud Run, and the fewer moving parts in that path the better.

Every value that already exists in `.env.example`, spec 09 or spec 11 is *copied*, never
invented — queue rates, model IDs, thresholds and caps are part of the contract.
"""

from __future__ import annotations

import functools
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Content types we accept (spec 01 §3). heif is the other MIME iOS sometimes sends for HEIC.
PHOTO_CONTENT_TYPES = frozenset(
    {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"}
)
VIDEO_CONTENT_TYPES = frozenset({"video/mp4", "video/quicktime"})
ALLOWED_CONTENT_TYPES = PHOTO_CONTENT_TYPES | VIDEO_CONTENT_TYPES

# Size caps (spec 01 §2.2). Enforced twice: pinned into the signed URL so GCS rejects a
# mismatched PUT, and re-checked against real object size at intake.
MAX_PHOTO_BYTES = 20 * 1024 * 1024
MAX_VIDEO_BYTES = 200 * 1024 * 1024

MAX_FILES_PER_CALL = 50  # spec 01 §3
SIGNED_URL_TTL_MINUTES = 15  # spec 01 §3

# Derived render sizes (spec 01 §4). classify_768 is what Gemini sees.
THUMB_PX = 384
CLASSIFY_PX = 768
DISPLAY_PX = 1600

EXT_BY_CONTENT_TYPE = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/heic": "heic",
    "image/heif": "heic",
    "video/mp4": "mp4",
    "video/quicktime": "mov",
}


def _load_dotenv() -> None:
    """Load repo-root `.env` into os.environ for local runs (existing vars win).

    On Cloud Run there is no `.env` — config arrives as env vars — so this is a no-op there.
    """
    path = REPO_ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.split(" #", 1)[0].strip()
        if key and key not in os.environ:
            os.environ[key] = value


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


def _int_env(key: str, default: int) -> int:
    raw = _env(key)
    return int(raw) if raw else default


def _float_env(key: str, default: float) -> float:
    raw = _env(key)
    return float(raw) if raw else default


class Settings:
    """Resolved config. Instantiate via `settings()` — it is cached per process."""

    def __init__(self) -> None:
        _load_dotenv()

        self.service: str = _env("SERVICE", "api")
        self.environment: str = _env("ENVIRONMENT", "development")
        self.port: int = _int_env("PORT", 8080)

        self.project: str = _env("GOOGLE_CLOUD_PROJECT")
        self.location: str = _env("GOOGLE_CLOUD_LOCATION", "us-central1")

        self.raw_bucket: str = _env("RAW_MEDIA_BUCKET")
        self.derived_bucket: str = _env("DERIVED_MEDIA_BUCKET")
        self.curated_bucket: str = _env("CURATED_REELS_BUCKET")

        # Cloud Tasks queues — rates live in deploy/queues.sh (spec 09 §2), names here.
        self.classify_queue: str = _env("CLASSIFY_QUEUE", "classify-queue")
        self.face_queue: str = _env("FACE_QUEUE", "face-queue")
        self.safety_queue: str = _env("SAFETY_QUEUE", "safety-queue")
        self.video_prep_queue: str = _env("VIDEO_PREP_QUEUE", "video-prep-queue")
        self.priority_queue: str = _env("PRIORITY_QUEUE", "priority-queue")
        self.renders_queue: str = _env("RENDERS_QUEUE", "renders-queue")

        # Task target services. Empty until the B2 workers deploy — `tasks.enqueue`
        # logs a skipped dispatch instead of queueing work nothing can consume yet.
        self.curate_url: str = _env("WORKER_CURATE_URL")
        self.face_url: str = _env("WORKER_FACE_URL")
        self.safety_url: str = _env("WORKER_SAFETY_URL")
        self.video_prep_url: str = _env("WORKER_VIDEO_PREP_URL")

        # OIDC identity Cloud Tasks uses when calling a worker; also the signBlob identity.
        self.tasks_sa_email: str = _env("TASKS_SA_EMAIL")
        self.signer_sa_email: str = _env("SIGNER_SA_EMAIL")

        # Product limits (spec 01 §3, spec 02 §3, spec 04 §2, spec 11 §1).
        self.upload_rate_limit_per_hour: int = _int_env("UPLOAD_RATE_LIMIT_PER_HOUR", 300)
        self.claim_review_threshold: int = _int_env("CLAIM_REVIEW_THRESHOLD", 8)
        self.default_public_floor: float = _float_env("DEFAULT_PUBLIC_FLOOR", 0.45)
        self.max_concurrent_live_events: int = _int_env("MAX_CONCURRENT_LIVE_EVENTS", 3)
        self.public_event_max_live_minutes: int = _int_env("PUBLIC_EVENT_MAX_LIVE_MINUTES", 60)
        self.public_event_cost_ceiling_usd: float = _float_env("PUBLIC_EVENT_COST_CEILING_USD", 3.0)

    def queue_path(self, queue: str) -> str:
        return f"projects/{self.project}/locations/{self.location}/queues/{queue}"

    def require(self, *attrs: str) -> None:
        """Fail fast at startup rather than mid-request on a missing env var."""
        missing = [a for a in attrs if not getattr(self, a, "")]
        if missing:
            raise RuntimeError(f"missing required configuration: {', '.join(missing)}")


@functools.lru_cache(maxsize=1)
def settings() -> Settings:
    return Settings()
