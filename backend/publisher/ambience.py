"""The wall's own soundtrack — a Lyria bed under the photographs, chosen from what is on screen.

Until now the only audio this system produced was a reel's score, so a wall showing photographs was a
silent wall: minutes of stillness between premieres, in a room where the whole point is that something
is happening. This composes an ambient bed for the wall itself and keeps it playing underneath.

**Where the mood comes from, and why no model decides it.** Four signals, all already maintained for
other reasons, none of them a new query:

  1. the dominant `sceneSetting` across the coverage shards (`shared/coverage.py::scene_totals`) — the
     same histogram the `onTopic` ranking term reads. A room, a street and open country are three
     different pieces of music.
  2. the crowd size, from the `peopleBuckets` histogram spec 13 added for the group-coverage gap. Twelve
     people in frame is not the same event as one person in frame.
  3. the active stage's `theme`, when the host set one. Those eight names (`gold`, `violet`, `crimson`,
     `ocean`, `forest`, `neon`, `slate`, `sunrise`) already drive the wall's colour palette in
     `tokens.css`; they are mood words, and using them for the sonic palette as well is symmetry rather
     than a second vocabulary to keep in sync.
  4. the stage's own label and the event's cultural glossary — the host's words, so the music is
     described in their terms and not ours.

The mapping from those to a `MusicBrief` is **deterministic arithmetic and a lookup**, not an LLM call,
and that is a deliberate line rather than a shortcut. "Twelve people, outdoors, at high aesthetic" → "up
tempo, bright, percussive" is a mapping; it requires no judgment, and this project's rule is that a
model is for judgments. Lyria is still the creative step — it turns the brief into music — so the brief
only has to be *accurate*. It also means a wall never waits on a reasoning call to have sound, and a
model outage costs nothing here.

**Cost is bounded by caching, not by a rate limit.** One track per (event, mood) forever: the mood key
is a small closed space, so a real event settles into a handful of tracks and re-opening the wall a
thousand times costs nothing. `MAX_TRACKS_PER_EVENT` is the backstop for a pathological stage table —
past it, the newest existing track is reused rather than a new one composed. A standing demo event that
runs for months therefore has a hard, knowable ceiling on this feature: 12 × $0.04.

**Composition is claimed transactionally**, because two televisions opening the same wall at the same
moment must not both pay Lyria. The first caller creates the document with `status: composing` and does
the work; the second sees `composing` and is told to come back.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from google.cloud import firestore

from schemas.common import UNINFORMATIVE_SETTINGS
from schemas.reel import MusicBrief
from shared import coverage, fs, gcs, log
from shared.settings import settings
from shared.stages import resolve_active
from shared.ulid import new_ulid

#: The ceiling on how many distinct ambient tracks one event will ever pay for. Twelve covers a wedding's
#: stages or a five-day trip's days with room to spare; past it the newest track is reused. Deliberately
#: a module constant rather than an env var — nothing reads it from the environment, and a
#: documented-but-unread env var is the `renders-queue` smell this project already names once.
MAX_TRACKS_PER_EVENT = 12

#: How long a `composing` claim is honoured before another caller may take it over. Lyria's own retry
#: budget inside `music.compose` is two attempts with a 3 s gap, so a stuck claim is a crashed instance
#: rather than a slow one.
CLAIM_STALE_SECONDS = 180

#: Lyria clips are ~30 s. The wall loops one, so the prompt asks for something that survives looping.
AMBIENCE_SECONDS = 30


@dataclass(frozen=True)
class Mood:
    """What the wall currently feels like, reduced to something cacheable."""

    setting: str = ""
    crowd: str = "small"
    theme: str = ""
    stage_label: str = ""
    glossary: tuple[str, ...] = ()

    @property
    def key(self) -> str:
        """The cache key, and therefore the unit of spend. Only the three *closed* dimensions are in
        it: the stage label and the glossary shape the prompt but must not fragment the cache, or a host
        renaming a stage would silently buy another track."""
        return f"{self.theme or 'none'}~{self.setting or 'none'}~{self.crowd}"


@dataclass
class Ambience:
    ok: bool
    status: str = "ready"  # ready | composing | unavailable
    mood_key: str = ""
    gcs_uri: str | None = None
    caption: str = ""
    tempo_bpm: float = 0.0
    duration_sec: float = 0.0
    reason: str = ""


# ---------------------------------------------------------------- reading the mood


#: The two thresholds that turn an average head-count into one of three sizes. A duet and a trio want the
#: same bed; a wedding party and a lone portrait do not.
CROWD_SOLO_BELOW = 2.0
CROWD_CROWD_ABOVE = 6.5


def _crowd_from_buckets(people: dict[str, int]) -> str:
    """A **weighted mean** head-count, banded — not the modal bucket.

    The mode is unstable on a histogram this coarse: a wedding whose photos ran `p1: 6, p4_6: 5,
    p7_12: 1` has a modal bucket of `p1` and would be scored "solo", so six group shots would be
    outvoted by a one-photo margin and the whole wall would get intimate music. Worse, the next portrait
    to land could flip it back, and each flip is a different mood key and therefore another Lyria clip.

    Weighting each bucket by its own floor (`shared/coverage.py::PEOPLE_BUCKET_FLOORS`) gives that event
    (1×6 + 4×5 + 7×1) / 12 ≈ 2.75 → "small", which is what a room looking at those photographs would
    say. It is a mean of lower bounds, so it reads slightly conservative by construction — the right
    direction to be wrong in, since over-calling a crowd would put party music under a portrait.
    """
    total = sum(max(0, int(n or 0)) for n in people.values())
    if total <= 0:
        return "small"
    weighted = sum(
        coverage.PEOPLE_BUCKET_FLOORS.get(bucket, 1) * max(0, int(count or 0))
        for bucket, count in people.items()
    )
    mean = weighted / total
    if mean < CROWD_SOLO_BELOW:
        return "solo"
    if mean > CROWD_CROWD_ABOVE:
        return "crowd"
    return "small"


def mood_of(event_id: str, event: dict) -> Mood:
    """Read the wall's mood off aggregates that already exist. No media documents are fetched."""
    stages = list(event.get("stages") or [])
    active, _source = resolve_active(event)
    theme, label = "", ""
    for stage in stages:
        if str(stage.get("stageId")) == active:
            theme = str(stage.get("theme") or "")
            label = str(stage.get("label") or "")
            break

    shards = coverage.read(event_id)

    # Dominant *informative* setting. `closeup_detail`/`screen_or_document`/`unknown` describe a frame,
    # not a place, so they cannot describe a room's music either — the same exclusion `onTopic` makes.
    totals = coverage.scene_totals(shards)
    informative = {k: v for k, v in totals.items() if k not in UNINFORMATIVE_SETTINGS and v > 0}
    setting = max(informative, key=lambda k: informative[k]) if informative else ""

    people: dict[str, int] = {}
    for shard in shards.values():
        for bucket, count in (shard.people_buckets or {}).items():
            people[bucket] = people.get(bucket, 0) + int(count or 0)
    crowd = _crowd_from_buckets(people)

    glossary = tuple(
        str(term)[:40]
        for term in ((event.get("eventTypeProfile") or {}).get("culturalGlossary") or [])
    )[:6]
    return Mood(setting=setting, crowd=crowd, theme=theme, stage_label=label[:80], glossary=glossary)


# ---------------------------------------------------------------- the brief


#: Setting → (style words, instrument palette). The wall is showing a place; this is what that place
#: sounds like. Every value is a *description handed to Lyria*, never a synthesis parameter — the model
#: is what turns these into music.
_SETTING_VOICE: dict[str, tuple[str, tuple[str, ...]]] = {
    "outdoor_nature": ("open-air, wide and unhurried", ("sustained strings", "soft pads", "acoustic guitar")),
    "outdoor_venue": ("warm outdoor celebration, golden and social", ("nylon guitar", "light percussion", "upright bass")),
    "indoor_venue": ("warm room tone with a gentle lift", ("felt piano", "brushed drums", "double bass")),
    "domestic_interior": ("intimate and close, almost private", ("felt piano", "soft synth pad")),
    "street": ("city motion, easy forward pulse", ("muted electric guitar", "shaker", "electric bass")),
    "vehicle": ("travelling, steady horizon", ("arpeggiated synth", "soft kick", "pads")),
}
_DEFAULT_VOICE = ("understated and warm, easy to talk over", ("felt piano", "soft pads"))

#: Crowd → (tempo, arc). Tempo is the one number here, and it is the honest one: more people in frame is
#: a busier room, and a busier room wants a faster pulse. Kept well inside `MusicBrief`'s 50–180 bounds.
_CROWD_VOICE: dict[str, tuple[int, str]] = {
    "solo": (72, "still and even throughout, no build"),
    "small": (92, "gently warm, holding one level"),
    "crowd": (112, "buoyant and social, holding one level"),
}

#: The eight kiosk palette names, as musical colour rather than visual. Same vocabulary as
#: `tokens.css`'s `[data-stage-theme]` blocks, on purpose (see the module docstring).
_THEME_COLOUR: dict[str, str] = {
    "gold": "burnished and celebratory",
    "violet": "dusky and late-evening",
    "crimson": "rich and close",
    "ocean": "cool, open and airy",
    "forest": "green, calm and organic",
    "neon": "electric and nocturnal",
    "slate": "muted, grey-light and quiet",
    "sunrise": "clear, bright and early",
}


def brief_for(mood: Mood) -> MusicBrief:
    """Mood → `MusicBrief`. Pure, so the whole mapping is checkable without a network."""
    style_words, instruments = _SETTING_VOICE.get(mood.setting, _DEFAULT_VOICE)
    tempo, arc = _CROWD_VOICE.get(mood.crowd, _CROWD_VOICE["small"])
    colour = _THEME_COLOUR.get(mood.theme, "")
    style = ", ".join(part for part in (colour, style_words) if part)
    return MusicBrief(
        style=style,
        tempoBpm=tempo,
        arc=arc,
        instruments=list(instruments),
        culturalRefs=list(mood.glossary),
    )


def prompt_for(mood: Mood, brief: MusicBrief, *, seed: int) -> str:
    """The ambient prompt. Deliberately not `music.prompt_for`, which asks for a 30-second film score
    that builds to a peak — the opposite of what should happen under photographs for an hour."""
    parts = [
        f"Instrumental background music for a live event photo wall, {AMBIENCE_SECONDS} seconds, "
        f"designed to loop seamlessly and play quietly under conversation.",
        f"Character: {brief.style or 'understated and warm'}.",
        f"Tempo around {brief.tempoBpm} BPM, steady.",
        f"Dynamics: {brief.arc}. No dramatic build, no crescendo, no hard ending, no fade-out — "
        f"it has to loop back on itself without a seam.",
    ]
    if brief.instruments:
        parts.append("Instrumentation: " + ", ".join(brief.instruments) + ".")
    if brief.culturalRefs:
        parts.append("Local colour, used lightly: " + ", ".join(brief.culturalRefs) + ".")
    if mood.stage_label:
        # The host's own words for what is happening. Last, and framed as a hint, so a stage called
        # "Last Call" tints the music without the model trying to score a literal scene.
        parts.append(f'The moment is called "{mood.stage_label}" — let that colour it faintly.')
    parts.append(
        "No vocals, no spoken word, nothing attention-seeking: the photographs are the subject. "
        f"Variation seed {seed % 10000}."
    )
    return " ".join(parts)


# ---------------------------------------------------------------- the cache


def _col(event_id: str):
    return fs.event_ref(event_id).collection("ambience")


def _ref(event_id: str, mood_key: str):
    return _col(event_id).document(mood_key)


def get(event_id: str, mood_key: str) -> dict | None:
    snap = _ref(event_id, mood_key).get()
    return snap.to_dict() if snap.exists else None


def _newest_ready(event_id: str) -> dict | None:
    """Any finished track for this event, newest first. What the ceiling falls back to — music that does
    not quite match the moment is a better wall than silence, and it costs nothing."""
    best: tuple[dt.datetime, dict] | None = None
    for snap in _col(event_id).stream():
        doc = snap.to_dict() or {}
        if not doc.get("gcsUri"):
            continue
        at = doc.get("createdAt")
        stamp = at if isinstance(at, dt.datetime) else dt.datetime.min.replace(tzinfo=dt.timezone.utc)
        if best is None or stamp > best[0]:
            best = (stamp, doc)
    return best[1] if best else None


@firestore.transactional
def _claim(transaction, ref, mood_key: str, now: dt.datetime) -> tuple[str, dict | None]:
    """Create-if-absent, so exactly one caller composes. Returns (`outcome`, existing doc)."""
    snap = ref.get(transaction=transaction)
    doc = snap.to_dict() if snap.exists else None
    if doc and doc.get("gcsUri"):
        return "ready", doc
    if doc and doc.get("status") == "composing":
        started = doc.get("startedAt")
        fresh = isinstance(started, dt.datetime) and (now - started).total_seconds() < CLAIM_STALE_SECONDS
        if fresh:
            return "composing", doc
    transaction.set(
        ref,
        {
            "moodKey": mood_key,
            "status": "composing",
            "startedAt": fs.SERVER_TIMESTAMP,
            "trackId": new_ulid(),
        },
        merge=True,
    )
    return "claimed", doc


def ensure(event_id: str, *, event: dict | None = None) -> Ambience:
    """The wall's current ambient track, composing it once if this mood has never been heard before.

    Never raises: every failure path returns `ok=False` with a reason, because a silent wall is a
    degradation and a 500 on the kiosk's audio request would be a bug in the wall itself.
    """
    from directors.reel import music  # noqa: PLC0415 - keep the workers' import path lean

    doc = event if event is not None else fs.get_event(event_id)
    if not doc:
        return Ambience(False, status="unavailable", reason="event does not exist")

    mood = mood_of(event_id, doc)
    key = mood.key
    ref = _ref(event_id, key)
    now = dt.datetime.now(dt.timezone.utc)

    # The hot path, and the one every wall takes after the first: a plain read, no transaction. Once a
    # mood has a track the answer can never change, so there is nothing for a transaction to protect —
    # and several televisions opening the same wall would otherwise contend on one document to be told
    # the same immutable thing.
    warm = ref.get()
    if warm.exists:
        warm_doc = warm.to_dict() or {}
        if warm_doc.get("gcsUri"):
            return Ambience(
                True,
                mood_key=key,
                gcs_uri=str(warm_doc.get("gcsUri")),
                caption=str(warm_doc.get("caption") or ""),
                tempo_bpm=float(warm_doc.get("tempoBpm") or 0.0),
                duration_sec=float(warm_doc.get("durationSec") or 0.0),
            )

    outcome, existing = _claim(fs.db().transaction(), ref, key, now)
    if outcome == "ready" and existing:
        return Ambience(
            True,
            mood_key=key,
            gcs_uri=str(existing.get("gcsUri")),
            caption=str(existing.get("caption") or ""),
            tempo_bpm=float(existing.get("tempoBpm") or 0.0),
            duration_sec=float(existing.get("durationSec") or 0.0),
        )
    if outcome == "composing":
        return Ambience(False, status="composing", mood_key=key, reason="a track for this mood is being composed")

    # We hold the claim. Check the ceiling before spending anything.
    count = sum(1 for _ in _col(event_id).list_documents())
    if count > MAX_TRACKS_PER_EVENT:
        fallback = _newest_ready(event_id)
        ref.delete()  # release the claim we just took; we are not going to compose against it
        if fallback:
            log.info("ambience_ceiling_reused", event_id=event_id, mood=key, cap=MAX_TRACKS_PER_EVENT)
            return Ambience(
                True,
                mood_key=str(fallback.get("moodKey") or ""),
                gcs_uri=str(fallback.get("gcsUri")),
                caption=str(fallback.get("caption") or ""),
                tempo_bpm=float(fallback.get("tempoBpm") or 0.0),
                duration_sec=float(fallback.get("durationSec") or 0.0),
            )
        return Ambience(False, status="unavailable", mood_key=key, reason="ambience track ceiling reached")

    brief = brief_for(mood)
    seed = abs(hash((event_id, key))) % 100000
    score = music.compose(brief, seed=seed, prompt=prompt_for(mood, brief, seed=seed))
    if score.silent or not score.audio:
        # Lyria is down or refused. Record the failure so the next wall opening retries rather than
        # inheriting a permanent dud, and let the caller run silent.
        ref.set(
            {"status": "failed", "failureReason": (score.failure or "no audio")[:300], "failedAt": fs.SERVER_TIMESTAMP},
            merge=True,
        )
        log.warn("ambience_degraded_to_silence", event_id=event_id, mood=key, err=score.failure)
        return Ambience(False, status="unavailable", mood_key=key, reason=score.failure or "no audio")

    uri = gcs.upload_bytes(
        settings().curated_bucket,
        f"{event_id}/ambience/{key}.mp3",
        score.audio,
        content_type=score.mime or "audio/mpeg",
        cache_control="public, max-age=31536000, immutable",
    )
    ref.set(
        {
            "moodKey": key,
            "status": "ready",
            "gcsUri": uri,
            "caption": (score.caption or "")[:1000],
            "tempoBpm": round(score.tempo, 2),
            "durationSec": round(score.duration, 3),
            "mood": {
                "setting": mood.setting,
                "crowd": mood.crowd,
                "theme": mood.theme,
                "stageLabel": mood.stage_label,
            },
            "brief": brief.model_dump(),
            "costUsd": score.cost_usd,
            "createdAt": fs.SERVER_TIMESTAMP,
        },
        merge=True,
    )
    log.info(
        "ambience_composed",
        event_id=event_id,
        mood=key,
        setting=mood.setting,
        crowd=mood.crowd,
        theme=mood.theme or "-",
        tempo=round(score.tempo, 1),
        seconds=round(score.duration, 1),
        cost_usd=score.cost_usd,
    )
    return Ambience(
        True,
        mood_key=key,
        gcs_uri=uri,
        caption=(score.caption or "")[:1000],
        tempo_bpm=score.tempo,
        duration_sec=score.duration,
    )
