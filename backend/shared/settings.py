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

#: NOT spec-pinned, and not env-overridable — same discipline as the Story Director rails below: a
#: rate limit an operator can widen from a deploy flag is a rate limit in name only. Spec 01 §3 caps
#: uploads per uid per hour and spec 02 §3 caps nothing at all on the enrollment path, which is how
#: one anonymous session could submit selfie after selfie until one crossed τ_claim against somebody
#: else's face — a brute-force search over the guest list, at InsightFace's expense. Six is generous
#: for the honest case (a first enrollment, a couple of retries in bad light, a re-claim on each of
#: two devices) and useless as a search budget. Held claims make each attempt visible to the host as
#: well, so the limit bounds the noise rather than being the only defence.
CLAIM_RATE_LIMIT_PER_HOUR = 6

#: How long the host's review-card selfie URL is good for. Much shorter than `RENDER_URL_TTL_MINUTES`
#: (60, `api/media.py`) on purpose: that one has to outlive a kiosk lingering on one photograph,
#: while this is an unaltered biometric being shown for a five-second decision, so the grant should
#: die with the review session rather than with the afternoon.
CLAIM_REVIEW_URL_TTL_MINUTES = 10

#: Page size for `GET /v1/events/{eventId}/claims`. A review queue is something a host empties, not
#: something they scroll: 50 outstanding claims at one event already means something is wrong and the
#: host needs to see *that*, not page 4 of it.
CLAIM_LIST_LIMIT = 50

#: Page size for `GET /v1/events/{eventId}/media/review-queue`, and how deep its scan goes. Same
#: reasoning as `CLAIM_LIST_LIMIT` — the queue exists to be emptied — but with one extra
#: consideration: the query filters `guardian.hostDecision` in Python (a composite index on a field
#: absent from almost every document would be an index serving a query that returns nothing), so
#: `REVIEW_QUEUE_SCAN` bounds the documents read and `REVIEW_QUEUE_LIMIT` bounds those returned. The
#: gap between them is what makes `truncated` meaningful: a scan that filled the page early has more
#: behind it. NOT spec-pinned (HANDOFF §9) — spec 03 §5.3 names the queue, never its page size.
REVIEW_QUEUE_LIMIT = 40
REVIEW_QUEUE_SCAN = 200

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

# --- video prep (spec 03 §4) ---------------------------------------------------------------
# The two the spec pins verbatim — "keyframes at 1 fps capped at 12 frames" and "proxy_720.mp4".
# `MAX_KEYFRAMES` is the whole cost story for video: 12 frames × ~258 tokens ≈ 3,100 input tokens,
# about two photos' worth, which is what keeps a clip inside the same spend rail as the stills the
# classify and safety queue rates were calibrated against (spec 09 §2).
KEYFRAME_FPS = 1
MAX_KEYFRAMES = 12
PROXY_HEIGHT = 720

#: How many frames the poster is chosen from, by sharpness. Spec 03 §4 says "best of 3 sampled
#: frames by sharpness" — three, verbatim.
POSTER_CANDIDATES = 3

#: NOT spec-pinned (HANDOFF §9). A ceiling on clip length, checked after `ffprobe` and before any
#: transcode. It exists because every cost in this worker scales with duration and the 200 MB upload
#: cap does not bound it usefully — a heavily-compressed 40-minute clip fits well inside 200 MB and
#: would occupy the 2-concurrency queue for minutes while the wall waits. Five minutes comfortably
#: holds anything a guest actually shoots at an event; longer is a screen recording or a mistake, and
#: rejecting it with a reason beats timing out with none.
MAX_VIDEO_DURATION_SEC = 300

#: Wall-clock budget for one ffmpeg invocation. Under Cloud Run's 300 s request timeout for this
#: service (deploy/up.sh) with room for the download, the probe and three uploads either side.
FFMPEG_TIMEOUT_SEC = 150

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
#: The production `director-tick` cadence, spec 09 §2 verbatim (`*/2 * * * *`), as seconds. Mirrored
#: here so `GET /v1/events/{id}/public` can tell a client how long until the next tick is due without
#: the client hardcoding a schedule it cannot see. **If `deploy/judge-mode.sh` re-schedules the job for
#: the judging month, this is the value that has to move with it** — the countdown is honest only while
#: the two agree, and a countdown that lies is worse than no countdown.
PRODUCTION_TICK_SECONDS = 120

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

# --- multi-day pacing (spec 13) ----------------------------------------------------------------
# None of these three is spec-05-pinned; all are spec 13's, chosen this build and flagged in
# HANDOFF §9 per the never-improvise discipline.
#: How long after a stage's `endsAt` its uncovered moments stay *live* gaps. Past this, they are
#: archived once into `directorState.permanentGaps` (the wrap report's honesty record) and stop
#: bidding for prompt slots and bounty budget — Day 1's missed dinner must not still be shouting
#: over Day 4's viewpoint. 90 min: long enough that "we're still at the restaurant" photos count.
STAGE_GAP_GRACE_MINUTES = 90
#: A tick is *idle* — deterministic steps only, no model call — when nothing is scheduled within
#: this many minutes ahead (nor within the grace window behind), nobody has uploaded, no bounty is
#: open and the host is not holding a stage. A 5-day trip is ~3,600 ticks; overnight ones are ~90%
#: of them, and every one used to be a paid `gemini-3.7-flash` call.
TICK_IDLE_LOOKAHEAD_MINUTES = 120
#: Spec 13's evidence-driven advance: a drift signal naming the *same* target stage on this many
#: consecutive ticks (at REASON confidence ≥ `STAGE_ADVANCE_MIN_CONFIDENCE`) may advance the stage
#: even outside `STAGE_ADVANCE_WINDOW_MINUTES` — the photos move the timeline; the schedule only
#: anticipates it. One tick's drift can be a burst of forwarded photos; two consecutive is a place.
DRIFT_ADVANCE_TICKS = 2

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
#: NOT spec-pinned. How recently a bounty must have been escalated to still deserve the whole screen
#: (spec 04 §4's `bounty_call` takeover). Spec 05 §3 escalates an unfulfilled bounty at half-life and
#: spec 04 §4 gives an escalated one the lead slot, and neither says when that claim expires — so on an
#: event where nobody submits, the escalate → expire → reissue cycle holds the wall indefinitely.
#: Measured on `dev_demo`: 12 of 16 bounties ended with `kioskTakeover: true`. On a real wedding a
#: submission breaks the loop; on the judge event, where visitors arrive hours apart, nothing does.
#: 12 minutes is long enough that the takeover is unmissable and short enough that the wall goes back
#: to photographs — the poster becomes punctuation instead of nagging. An escalated bounty that ages
#: out still banners in every guest's pocket; it just stops owning the five-metre screen.
KIOSK_TAKEOVER_FRESH_MINUTES = 12
#: Spec 11 §3.3, verbatim: tier → kiosk hero-score multiplier, taken as the max across faces in
#: frame. Deterministic metadata, never a model's opinion — VIP is policy, not memory (spec 11 §4).
VIP_WEIGHT_BY_TIER = {0: 3.0, 1: 1.8, 2: 1.3, 3: 1.0}
DEFAULT_TIER = 3

# --- Reel Director rails (spec 06) ------------------------------------------------------------
#
# Spec 06 pins the shot-count band and the cost budget; everything else here is this build's choice,
# flagged in HANDOFF §9 rather than silently made (same discipline as τ_claim §4.16, the Guardian's
# reason codes, and the kiosk constants §4.20). None is env-overridable: the shot band and the
# aesthetic floor are what stop a reel from being a slideshow of the worst photographs at the event.
REEL_MIN_SHOTS = 10  # spec 06 §2.3 verbatim ("shot count (10–24)")
REEL_MAX_SHOTS = 24  # spec 06 §2.3 verbatim
#: NOT spec-pinned. What the *prompt* asks for, as opposed to what the code enforces — and the gap
#: between the two is the point. `REEL_MIN_SHOTS` stays exactly spec 06 §2.3's floor for the finished
#: cut; but the linter drops near-duplicates *after* the model answers, so asking for the floor itself
#: means any drop at all lands under it. Measured on `dev_demo`: a plan answering 12 shots routinely
#: lost 3 to the near-duplicate rule and failed at 9. Three shots of headroom is the cheapest fix that
#: does not touch a spec value or weaken the output contract.
REEL_SHOT_REQUEST_MIN = REEL_MIN_SHOTS + 3
REEL_CANDIDATE_CAP = 40  # spec 06 §3 step 1 verbatim ("cap 40")
#: How many documents the SELECT query reads before diversity sampling cuts it to the cap. Wide
#: enough that sampling has something to choose between, narrow enough to stay one page.
REEL_CANDIDATE_FETCH = 150
#: NOT spec-pinned — spec 06 §3 step 1 says "aesthetic floor" without a number. 0.35 is the same bar
#: `directors/story/validate.py` uses to decide a bounty submission is worth points, and for the same
#: reason: below it a photograph is evidence that something happened, not something to look at.
REEL_AESTHETIC_FLOOR = 0.35
#: Output canvas (spec 06 §3 step 5 verbatim: 1080×1920 H.264).
REEL_WIDTH = 1080
REEL_HEIGHT = 1920
REEL_FPS = 30
#: Ken Burns zoom headroom. The render reads the `display_1600` derived variant (never an original —
#: `sa-render` has no raw-bucket grant), so 1.25× is about as far as a 1600 px source can be pushed
#: at 1080×1920 before the softness shows. It is also the maximum a face box can be scaled by, which
#: is what keeps `edl.py`'s containment proof cheap.
REEL_ZOOM = 1.25
#: Face safety margin, as a fraction of the frame, kept between a face box and the visible edge.
REEL_FACE_MARGIN = 0.04
REEL_TRANSITION_SEC = 0.5
REEL_MIN_SHOT_SEC = 1.2
REEL_MAX_SHOT_SEC = 4.5
#: Beat-snap tolerance spec 06 §8 asserts against ("cuts land within ±80 ms of beat times").
REEL_BEAT_TOLERANCE_MS = 80
#: The critic's pass mark. Below it, DIRECT runs once more with the critique appended (spec 06 §2.4's
#: "≤1 retry"). 0.6 is chosen so an ordinary storyboard passes first time and the deliberately-flat
#: fixture in `scripts/smoke_reel.py` does not.
REEL_CRITIC_PASS_SCORE = 0.6
#: Spec 06 §2.4's rubric floor: "references ≥ 3 specific moments by name".
REEL_CRITIC_MIN_MOMENTS = 3
#: Spec 06 §6's per-version budget, used for the cost line on the reel document: direct+critic ≈$0.02,
#: Lyria $0.04, render ≈$0.10 compute → <$0.20/version.
REEL_LYRIA_COST_USD = 0.04
REEL_RENDER_COST_USD = 0.10
#: How long a commission may sit in `directing`/`rendering` before another commission of the same
#: persona is allowed to start. Spec 06 §3 serialises per persona ("one active render each"); this is
#: the crash backstop, because the process holding the commission is a Cloud Run Job that can die.
REEL_STALE_MINUTES = 20
#: Spec 06 §5 verbatim: "Veo 3.1 Fast image-to-video 8 s opener from the top couple portrait".
REEL_OPENER_SECONDS = 8
REEL_OPENER_COST_PER_SECOND_USD = 0.10  # veo-3.1-fast, measured in the B1 risk probe

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
        #: Lyria 3 Clip — every reel's soundtrack (spec 06 §3 step 4, bonus +0.2). Reached through
        #: `interactions.create(stream=True)`, not `generate_content`, and only from `global`; see
        #: `directors/reel/music.py` for the rest of the undocumented call shape.
        self.model_music: str = _env("MODEL_MUSIC", "lyria-3-clip-preview")
        #: Veo 3.1 Fast's *Vertex* publisher ID (spec 06 §5's couple-reel opener, bonus +0.2) —
        #: deliberately a second key rather than an edit to `MODEL_VIDEO_GEN` (HANDOFF §9): that value
        #: is the AI Studio / Gemini API ID and 404s on the enterprise path this project always calls
        #: on. `directors/reel/opener.py` reads this one.
        self.model_video_gen_vertex: str = _env("MODEL_VIDEO_GEN_VERTEX", "veo-3.1-fast-generate-001")
        #: Gemma 4 — the private per-person taste memo (spec 07 §2, bonus +0.2). Free tier, off the
        #: critical path by design: a malformed or refused memo skips that cycle and nothing downstream
        #: gates on it (`directors/story/taste.py`).
        self.model_taste_memo: str = _env("MODEL_TASTE_MEMO", "gemma-4-26b-a4b-it")

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
        #: The Cloud Run **Job** that renders a reel (spec 09 §1: 8 vCPU / 32 GiB, task per
        #: commission). Not a Cloud Tasks target — Tasks speaks HTTP and a Job is started through the
        #: Run Admin API, so `shared/jobs.py` calls `run_job` instead. Empty means the same thing an
        #: empty worker URL means in `tasks.enqueue`: log a skipped launch, never silently pretend.
        self.render_job: str = _env("RENDER_JOB_NAME", "render")
        #: Public base URL of `api`, used to build the reel's playable `videoUri`. The kiosk's
        #: `<video src>` cannot carry an Authorization header, so the reel document stores a stable
        #: API URL that 302s to a short-lived signed GCS URL (`api/reels.py`), rather than storing a
        #: signed URL that would expire inside the document.
        self.api_base_url: str = _env("NEXT_PUBLIC_API_URL")

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


# ==================================================================== event membership (spec 02 §1)
#
# The seat cap on an invite-only event. NOT spec-pinned — no spec names a headcount — so it carries
# the same flagged-not-pinned discipline as the constants above, and it is a module constant rather
# than an env var for the same reason as the guardrails in this file: a limit an operator can widen
# from a deploy flag is a limit in name only, and this one belongs to the *host*, who can raise it
# per event from the console.
#
# **Seats, not people.** Spec 02 §1 deliberately gives one human several uids — phone, laptop, a
# rescan after clearing site data — so this counts sessions, and a family of four on shared devices
# can easily be a dozen. 300 is generous for the weddings this system is shaped around (a 200-guest
# wedding will not produce 300 uploading sessions) and it exists only so "invite-only" has a number
# at all: the failure it guards against is a link leaking onto a group chat, not a guest list being
# one over. A refused legitimate guest standing at the venue is a far worse outcome than one admitted
# stranger, which is why the default is high, `None` (uncapped) stays legal, and raising it is one
# host tap rather than a redeploy.
INVITE_DEFAULT_SEATS = 300

#: How many invite-code lookups one uid may attempt per hour at `POST /v1/events/join-code`.
#:
#: The code itself is `secrets.token_urlsafe(16)` — 128 bits, so guessing one is not a threat model and
#: this limit is not pretending to be the defence. What it bounds is the *cost* of somebody pointing a
#: script at the endpoint: every attempt is a Firestore query, and the endpoint is unauthenticated
#: beyond an anonymous token that anyone can mint. 20 is far more than a human mistyping a code off a
#: printed card will ever need, and it makes enumeration pointless rather than merely slow.
CODE_LOOKUP_RATE_LIMIT_PER_HOUR = 20

# --- sweeper ---
#
# `POST /internal/sweep` (spec 09 §2's `orphan-sweep`, hourly). None of these numbers is spec-pinned —
# spec 09 §2 names the four jobs the sweep does in one pass and spec 11 §1.3/§1.4 name the TTL and cost
# ceiling it enforces, but none of the *sweep's own* bounds are given a value anywhere. Recorded here
# and flagged for HANDOFF §9, same discipline as tau_claim (§4.16) and the kiosk constants (§4.20).
#
# A lease crash-backstop, not a rate limit: the job runs hourly, so 55 minutes only matters if a
# sweep dies mid-run — it must not survive long enough to block the *next* scheduled sweep.
SWEEP_LEASE_MINUTES = 55
#: Events read per run. Hackathon-scale: `MAX_CONCURRENT_LIVE_EVENTS=3` plus a handful of dev/wrapped
#: events, nowhere near this cap — it exists so a future high-volume deployment fails safe (a bounded
#: sweep that misses some events) rather than unboundedly (a sweep that never finishes).
SWEEP_MAX_EVENTS_PER_RUN = 200
#: Media docs read per event per case (A1/A4/A5), before the per-case action cap below stops it.
SWEEP_MEDIA_SCAN_LIMIT = 300
#: Actions taken per event per case per run (stages re-enqueued, intents abandoned, faces reconciled).
#: One enormous event must not turn an hourly sweep into a multi-minute one.
SWEEP_MAX_ACTIONS_PER_CASE = 30
#: A5 re-drives a stuck upload by re-running `intake.process()` in-process — real GCS/Pillow work, not
#: a queue enqueue — so its own cap is far tighter than the other cases' before it can eat the
#: Scheduler job's attempt deadline.
SWEEP_MAX_REDRIVES_PER_RUN = 5
#: A1: how stale a `pending` stage's `stageTimings.{stage}.queuedAt` must be before the sweep treats
#: it as stranded rather than merely in flight. Brief's own estimate ("~10 minutes"), not a spec value.
SWEEP_STRANDED_STAGE_MINUTES = 10
#: A5: same reasoning, for a media doc stuck at `status=='uploaded'` (the claim landed, nothing since).
SWEEP_STUCK_UPLOAD_MINUTES = 10
#: A4: generous slack *beyond* `SIGNED_URL_TTL_MINUTES` before an `awaiting_upload` intent is declared
#: abandoned — the client outbox retries for a while, and a slow phone on bad venue wifi is not abuse.
SWEEP_ABANDONED_SLACK_MINUTES = 45
#: A3: how old a raw-bucket object must be, with no matching media doc, before it is an orphan rather
#: than a normal race between the PUT landing and the finalize event being handled.
SWEEP_ORPHAN_MIN_AGE_MINUTES = 60
#: A3: raw-bucket objects listed per run (a `list_blobs` call, not a Firestore read).
SWEEP_ORPHAN_SCAN_LIMIT = 500
#: A2: face docs read per event per run before cluster-centroid reconciliation. Comparison is
#: pairwise over the *distinct clusters* found, not over this count, so this bound is what keeps a
#: pathological event's face count from making the pairwise pass itself unbounded.
SWEEP_FACE_SCAN_LIMIT = 1500

# --- world model (spec 03 §5.1's `sceneSetting`; MAIN lane) -----------------------------------
#
# The relevance rails. Nothing here is spec-pinned — no spec has a notion of topicality at all, which
# is itself the finding: `shared/visibility.py::decide` has six inputs and none of them asks whether a
# photo is *about* the event. All four are flagged for HANDOFF §9.
#
# Read by `publisher/program.py`'s `onTopic` term, which is why they live here rather than beside the
# distiller in `directors/story/world.py`: that module is a service-side consumer, but `program.py` is
# a pure function whose only permitted import is this file.

#: Below this many placed photos the mechanism is **off** — `onTopic` is 1.0 for everything.
#:
#: A distribution needs a distribution. Photo #1 is 100% of the corpus, so it is simultaneously the
#: baseline and an outlier; at photo 15 of a wedding that started indoors, `outdoor_venue` is at 0% and
#: the baraat — the most important sequence of the day — would read as a 100% outlier and be demoted on
#: the wall. Fifty is where a share becomes a statement rather than an accident.
#:
#: This is the same reasoning `STAGE_PRIOR_FLAT` encodes above: a flat prior contributes no ordering
#: information, which is the honest thing to contribute when you know nothing yet. Not a threshold
#: below which the system guesses — one below which it declines to.
WORLD_MIN_CORPUS = 50

#: Corpus share at or above which a setting is simply normal for this event, and `onTopic` is 1.0.
#: 10% is deliberately generous: the cost of wrongly demoting a legitimate photo is invisible and
#: unrecoverable to the guest who took it, and the cost of a stray hike reaching the wall is six
#: seconds of mild embarrassment. The asymmetry should be reflected in the numbers, not just noted.
WORLD_ONTOPIC_COMMON_SHARE = 0.10

#: Below this share a setting is a genuine outlier for this event. Between the two it is unusual but
#: not rare, and takes the middle multiplier.
WORLD_ONTOPIC_RARE_SHARE = 0.02

#: The three `onTopic` multipliers: normal, unusual, outlier. A *demotion*, never a gate — the term
#: multiplies into the hero score and touches nothing else. It is never an input to
#: `recompute_visibility`, and the arithmetic is why: at a plausible 95% precision on a 2,000-photo
#: event where 1% is genuinely off-topic, you get ~19 true positives and ~99 false positives. As a
#: ranking factor a false positive costs a photo one hero slot and nothing else — it keeps its gallery
#: entry, its albums, its reel eligibility and its owner. As an exposure gate the same error would
#: suppress 99 legitimate photos, and until the review queue existed there was no way to release them.
WORLD_ONTOPIC_WEIGHTS = (1.0, 0.5, 0.15)

# --- pipeline spend roll-up (spec 09 §2; MAIN lane) --------------------------------------------
#
# `event.costSoFarUsd` is read in three places — the host console's "Pipeline Spend" KPI
# (`api/host.py::console_summary`), the sweep's public-event cost ceiling, and the frontend — and until
# now was written by **nobody**. So the KPI read $0.00 forever and the ceiling could never fire. Found
# by the sweeper lane, which correctly declined to reach into the media pipeline to fix it.
#
# The fix is a *derivation*, not a stored counter: `shared/spend.py` sums the per-media token counters
# that every worker already writes (`services/gemini.py::usage_increments`) with one Firestore `sum()`
# aggregation. Deliberately not an `Increment` on the event document — spec 09 §2 runs the two Gemini
# queues at 8/s each, so up to 16 writes/second would land on one document, and Firestore's sustained
# per-document write ceiling is ~1/s. That is the hot-key problem `fs.py::coverage_stage_shards_col`
# already explains for the coverage ledger, and the same answer applies: do not create a hot document.

#: Blended USD per Gemini token, **derived from spec 09 §2's own published figure** rather than
#: invented: "~1,548 tokens in + ~300 out ≈ $0.0012/photo" → 0.0012 / 1848 ≈ 6.49e-7 per token.
#:
#: Blended across input and output because spec 09 gives one combined number and splitting it would
#: mean inventing the ratio. Deriving from measured tokens rather than assuming $0.0012 per call is the
#: point: a video's 12-keyframe classify call really does cost about twice a still's, and a ticker that
#: charged both the same would understate exactly when spend starts mattering.
GEMINI_BLENDED_USD_PER_TOKEN = 0.0012 / 1848

#: Vision SafeSearch, per image, first pass of the Guardian. NOT in spec 09 (which prices only the
#: Gemini queues) — Google's published SafeSearch rate at the 1k-5k/month tier. Flagged for HANDOFF §9.
#: Counted per *screened item* rather than per keyframe: `workers/safety` calls SafeSearch once, on the
#: classify render or the poster, whatever the media kind.
VISION_SAFESEARCH_USD_PER_IMAGE = 0.0015
