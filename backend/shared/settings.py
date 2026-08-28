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

# --- face index rails ------------------------------------------------------------------------
# The two numbers spec 02 §3.1 pins for the claim-size gate's review card and magic links.
CLAIM_EXEMPLARS = 4
CLAIM_LINK_TTL_DAYS = 30

#: A selfie arrives as base64 in a JSON body; anything larger than this is a mistake or an attack.
SELFIE_MAX_BYTES = 8 * 1024 * 1024

#: Engineering rails, not product policy — they bound write amplification, they do not decide
#: anything. A 60-face baraat photo is a crowd shot, not 60 album memberships, and one media doc
#: should never fan out into hundreds of face docs; faces are kept largest-box-first, so the
#: people the photo is *about* are the ones that survive the cut.
MAX_FACES_PER_MEDIA = 25
#: How many neighbours the face-level claim pulls. Well above CLAIM_REVIEW_THRESHOLD (8) so the
#: audited `faceCount` is the real number rather than a truncated one.
CLAIM_FACE_LIMIT = 100
#: Nearest-neighbour probe depth for cluster adoption. >1 because the nearest hits may be other
#: faces from the same photo, which are by definition *not* the same person.
CLUSTER_PROBE_LIMIT = 8

# Derived render sizes (spec 01 §4). classify_768 is what Gemini sees.
THUMB_PX = 384
CLASSIFY_PX = 768
DISPLAY_PX = 1600

# Stage fusion (spec 03 §5.1). The temporal prior is 1.0 inside a stage's scheduled window with a
# ±30 min ramp, 0.15 outside — and *flattened* to 0.5 everywhere when EXIF is missing, so a wrong
# upload-time prior can never outvote the visual signal on a WhatsApp forward.
STAGE_PRIOR_IN_WINDOW = 1.0
STAGE_PRIOR_OUT_OF_WINDOW = 0.15
STAGE_PRIOR_RAMP_MINUTES = 30
STAGE_PRIOR_FLAT = 0.5

# Cloud Tasks gives up after 5 attempts (spec 09 §2). The handler needs the same number to know
# which attempt is its last, because that is the one that must quarantine instead of retrying.
MAX_STAGE_ATTEMPTS = 5

# --- the autonomy spine (spec 05 §1, spec 04 §4, spec 09 §2/§5) -------------------------------
#
# Two leases, two different jobs, and the difference is worth stating once. The **tick lease** is
# mutual exclusion for an *action* — it is taken, the tick runs, it is released, and the TTL only
# matters if the holder dies mid-tick. Holding it for the full TTL would throttle the cadence
# instead of protecting it (a 5-minute hold against a 2-minute schedule would drop every other
# tick). The **publisher lease** is leadership over a *resource* for as long as an instance lives:
# it is renewed on a timer and expires if the holder stops renewing, which is what lets another
# instance take over an event whose publisher was scaled away.
TICK_LEASE_MINUTES = 5  # spec 05 §1, crash backstop only
PUBLISHER_LEASE_SECONDS = 120  # spec 04 §4 — "TTL 2 min, same pattern as the director tick lease"
PUBLISHER_RENEW_SECONDS = 45  # comfortably inside the TTL: two renewals may fail before failover
#: Spec 09 §2/§5: Scheduler's cron floor is 1 minute, so the demo tick fires at `* * * * *` and
#: each invocation enqueues one Cloud Task at +30 s hitting the same endpoint. The effective demo
#: cadence is 30 s and it is delivered server-side — a console loop reads as a button press.
DEMO_INTERLEAVE_SECONDS = 30

# --- Story Director rails (spec 05) -----------------------------------------------------------
#
# Everything spec 05 §1 pins is copied verbatim and marked so; the four values it leaves open are
# marked just as clearly and recorded in HANDOFF §9, same discipline as τ_claim (§4.16) and the two
# kiosk constants (§4.20). None of them is env-overridable on purpose: a guardrail an operator can
# widen from a deploy flag is a guardrail in name only.
DIRECTOR_MAX_NEW_BOUNTIES_PER_TICK = 2  # spec 05 §1 verbatim
DIRECTOR_MAX_ACTIVE_BOUNTIES = 6  # spec 05 §1 verbatim
BOUNTY_POINTS_MIN = 50  # spec 05 §1 verbatim: clamp(basePoints × vipWeight, 50, 300)
BOUNTY_POINTS_MAX = 300  # spec 05 §1 verbatim
STAGE_ADVANCE_MIN_CONFIDENCE = 0.8  # spec 05 §1 verbatim
STAGE_ADVANCE_WINDOW_MINUTES = 45  # spec 05 §1 verbatim ("scheduled window agrees ±45 min")
DIRECTOR_SESSION_WINDOW = 10  # spec 05 §1 verbatim ("rolling window of the last 10 tick summaries")
NEAR_STAGE_WINDOW_MINUTES = 15  # spec 05 §4 verbatim ("guests who uploaded in the last 15 min")
UPLOAD_VELOCITY_WINDOW_MINUTES = 5  # spec 05 §1 verbatim ("upload velocity (5-min window)")

#: NOT spec-pinned. The midpoint of the [50, 300] guardrail band, so a tier-3 gap pays the middle of
#: the range and a tier-0 gap (×3.0) saturates at the ceiling — which is exactly the ordering spec 11
#: §3.3 asks for ("the guardrail is the ceiling; tier is the reason a bride's-mother bounty outpays a
#: generic one"). The model may propose its own `basePoints`; this is the default and the clamp.
BOUNTY_DEFAULT_BASE_POINTS = 100
#: NOT spec-pinned. Spec 05 §3 has `expiresInMin` as a model-chosen field and §3's escalation fires
#: at "half-life", so the default has to be long enough for a guest to notice a banner, walk to the
#: right room and take a photograph, and short enough that a missed moment expires while the event
#: still remembers it. 20 minutes puts escalation at 10.
BOUNTY_DEFAULT_TTL_MINUTES = 20
BOUNTY_MIN_TTL_MINUTES = 5
BOUNTY_MAX_TTL_MINUTES = 90
#: NOT spec-pinned. Spec 05 §3's "partial credit (right moment, weak quality) → smaller award,
#: bounty stays open". Two fifths: visibly worth having, visibly not the whole prize.
BOUNTY_PARTIAL_FRACTION = 0.4
#: NOT spec-pinned. Spec 05 §4 requires a "per-uid daily points cap" without a number. 1,000 is
#: roughly ten maximum-value bounties — beyond any legitimate guest's day, and low enough that a
#: scripted submitter cannot own the leaderboard.
GUEST_DAILY_POINTS_CAP = 1000
#: The Curator's moment-match confidence a bounty submission must clear to be fulfilled rather than
#: partially credited. Not spec-pinned; deliberately strict, because a false fulfilment closes a
#: coverage gap that is still open.
BOUNTY_MATCH_CONFIDENCE = 0.6
#: How many recent indexed items the ledger reads for the stage-drift signal (spec 05 §2's "a run of
#: high-confidence off-schedule classifications, e.g. 12 of the last 20"). 20 is that sentence.
DRIFT_SAMPLE_SIZE = 20
#: A visual score this high for a stage other than the active one counts as one drift vote.
DRIFT_MIN_VISUAL = 0.6
#: And this many votes out of `DRIFT_SAMPLE_SIZE` make it a signal worth reasoning about, rather than
#: two guests wandering off. Spec 05 §2's example is 12 of 20; 0.5 is that, rounded to a fraction so
#: it still means something when the sample is short.
DRIFT_VOTE_FRACTION = 0.5

# --- kiosk program rails (spec 04 §4) ---------------------------------------------------------
KIOSK_PROGRAM_SECONDS = 300  # "~5 min program, recomputed on triggers"
KIOSK_HERO_HOLD_SEC = 6  # spec 04 §4's slot sketch, verbatim
KIOSK_HERO_SHARE = 0.60  # "~60% — fresh highlights of the active stage"
KIOSK_LEADERBOARD_EVERY_SEC = 90  # "every ~90s"
KIOSK_JUST_IN_WINDOW_SEC = 120  # spec 04 §4's `liveWindowSec: 120`
KIOSK_RECENCY_HALF_LIFE_MIN = 20  # "recencyDecay(capturedAt, half-life 20 min)"
KIOSK_DIVERSITY_WINDOW = 5  # "don't show the same face-cluster or momentTag twice within 5 slots"
KIOSK_CANDIDATE_LIMIT = 60  # spec 04 §3's `limit(60)`, reused for the publisher's own query
#: Recompute even with no trigger, so a missed listener event cannot freeze the wall (spec 04 §4's
#: "every 5 min as fallback").
KIOSK_FALLBACK_SECONDS = 300
#: Coalesce a burst of listener callbacks into one recompute. A 20-photo batch lands as up to 20
#: snapshot events; rebuilding the program 20 times would write 20 revisions and reset the show on
#: each one.
KIOSK_DEBOUNCE_SECONDS = 1.5
#: Stage match multipliers. Active ×1.0 and previous ×0.4 are spec 04 §4 verbatim; the value for
#: every *other* stage is not pinned anywhere, so 0.2 is this build's choice — low enough that the
#: wall follows the event, high enough that an early-morning Haldi photo is still eligible during a
#: thinly-covered Pheras rather than the wall going empty. Recorded in HANDOFF §9 rather than
#: silently chosen. `None` (no stage) is treated as "other".
KIOSK_STAGE_MATCH_ACTIVE = 1.0
KIOSK_STAGE_MATCH_PREVIOUS = 0.4
KIOSK_STAGE_MATCH_OTHER = 0.2
#: Applied when a candidate repeats a face cluster or moment tag already used inside the diversity
#: window. Also not spec-pinned: the spec names `diversityPenalty` as a factor without a value. 0.35
#: is chosen so it re-orders aggressively (a second groom photo loses to almost anything else) while
#: still letting a repeat win when the event genuinely has nothing else — a hard exclusion would
#: empty the wall at a five-guest party.
KIOSK_DIVERSITY_PENALTY = 0.35
#: Spec 11 §3.3, verbatim: tier → kiosk hero-score multiplier, taken as the max across faces in
#: frame. Deterministic metadata, never a model's opinion — VIP is policy, not memory (spec 11 §4).
VIP_WEIGHT_BY_TIER = {0: 3.0, 1: 1.8, 2: 1.3, 3: 1.0}
DEFAULT_TIER = 3

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
        #: GenAI publisher models serve from `global`; a us-central1 call 404s. Kept separate
        #: from `location` on purpose — that one builds Cloud Tasks queue paths.
        self.genai_location: str = _env("GENAI_LOCATION", "global")

        # Models — verbatim from .env.example, which postdates every model's training data.
        self.model_classifier: str = _env("MODEL_CLASSIFIER", "gemini-3.5-flash-lite")
        self.model_director: str = _env("MODEL_DIRECTOR", "gemini-3.7-flash")
        self.model_image_edit: str = _env("MODEL_IMAGE_EDIT", "gemini-3.1-flash-image")

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
        #: The publisher is not a Cloud Tasks target — it is a listener service (spec 04 §4). `api`
        #: calls it directly so a Scheduler tick can nudge the playlist even on a judge-month
        #: deployment where `min-instances` is 0 and the listener is not running.
        self.publisher_url: str = _env("PUBLISHER_URL")

        # OIDC identity Cloud Tasks uses when calling a worker; also the signBlob identity.
        self.tasks_sa_email: str = _env("TASKS_SA_EMAIL")
        self.signer_sa_email: str = _env("SIGNER_SA_EMAIL")
        #: The identity Cloud Scheduler presents to `/internal/tick` (deploy/sa.sh writes it).
        #: `api` is the one public service, so this allowlist is the tick's real access control.
        self.scheduler_sa_email: str = _env("SCHEDULER_SA_EMAIL")

        #: Where `buffalo_l` was baked (backend/docker/Dockerfile.face). insightface ignores
        #: INSIGHTFACE_HOME and resolves models from its `root=` kwarg only, so this value must
        #: match the Dockerfile's ENV or the worker silently re-downloads 326 MB on every boot.
        self.face_model_root: str = _env("MODEL_ROOT", "/models")

        # Face identity thresholds, all cosine similarity on unit-norm embeddings.
        #
        # τ_match and τ_cluster are spec 03 §5.2 verbatim. τ_claim is the one number in this
        # session that no spec pins: spec 02 §3 fixes only its *shape* — "strict, higher than the
        # photo-matching threshold" — so 0.60 is this build's choice, set deliberately above
        # τ_cluster so a claim can never be looser than the clustering that grouped the faces it
        # is claiming. The ambiguity margin is spec 02 §3 verbatim (twins and siblings).
        # All four are env-overridable because spec 03 §5.2 calls for Day-3 calibration.
        self.tau_match: float = _float_env("TAU_MATCH", 0.45)
        self.tau_cluster: float = _float_env("TAU_CLUSTER", 0.55)
        self.tau_claim: float = _float_env("TAU_CLAIM", 0.60)
        self.claim_ambiguity_margin: float = _float_env("CLAIM_AMBIGUITY_MARGIN", 0.08)

        #: Where the claim magic link points (spec 02 §3.1 — the code rides in the fragment).
        self.app_origin: str = _env("NEXT_PUBLIC_APP_ORIGIN")

        #: A Vertex AI Agent Engine resource id, when one exists. Empty is the normal case and not a
        #: degraded one: it selects the Firestore-backed read of the host's free-text preferences
        #: (`directors/story/memory.py`) over a Memory Bank recall of the same text at the same
        #: `{eventId}:host` scope. Nothing that gates a bounty, a point award or an exposure reads
        #: either path (spec 11 §4).
        self.agent_engine_id: str = _env("AGENT_ENGINE_ID")

        #: Model Armor template (text surfaces only — spec 03 §4.7). The *location* is read out of
        #: this resource name by `services/armor.py`, so it is configured exactly once: Model Armor
        #: is `us`/`eu` multi-region and lives on its own endpoint host, and a second copy of that
        #: value is how a 404 ends up looking like a permissions problem. Empty = unchecked, logged
        #: loudly, never silently treated as clean.
        self.model_armor_template: str = _env("MODEL_ARMOR_TEMPLATE")

        # Product limits (spec 01 §3, spec 02 §3, spec 04 §2, spec 11 §1).
        self.upload_rate_limit_per_hour: int = _int_env("UPLOAD_RATE_LIMIT_PER_HOUR", 300)
        self.claim_review_threshold: int = _int_env("CLAIM_REVIEW_THRESHOLD", 8)
        self.default_public_floor: float = _float_env("DEFAULT_PUBLIC_FLOOR", 0.45)
        self.max_concurrent_live_events: int = _int_env("MAX_CONCURRENT_LIVE_EVENTS", 3)
        self.public_event_max_live_minutes: int = _int_env("PUBLIC_EVENT_MAX_LIVE_MINUTES", 60)
        self.public_event_cost_ceiling_usd: float = _float_env("PUBLIC_EVENT_COST_CEILING_USD", 3.0)
        #: NOT spec-pinned. Spec 08 §1 says event creation is "unauthenticated create,
        #: rate-limited" without a number — same discipline as the other flagged-not-pinned
        #: constants above. 10/hour is generous for a legitimate host setting up co-hosts or
        #: retrying a mistake, and low enough that a scripted caller cannot cheaply farm slots
        #: against the spec 11 capacity cap.
        self.event_create_rate_limit_per_hour: int = _int_env("EVENT_CREATE_RATE_LIMIT_PER_HOUR", 10)

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
