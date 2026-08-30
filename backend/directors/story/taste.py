"""Taste memos — spec 07 §2: Gemma writes a 3-sentence memo from a deterministic tag-affinity
vector, off the critical path, whenever a person's reaction count crosses another multiple of 15.

**Cheap path** (HANDOFF's B4-S13 framing): the full swipe-deck card stack is cut. What ships instead
is a heart button on the private-album grid, writing the same document the swipe deck would have
written — `people/{personId}/reactions/{mediaId}: {verdict, at}`, `firestore.rules:159`'s "the one
client write in the entire system" — so nothing here, and nothing on the frontend, would need to
change if the full swipe UI is ever built.

**Why this runs from the tick and not a Firestore trigger.** Every event-driven surface in this
fleet is Cloud Storage → Eventarc (spec 03 §1); there is no Firestore-trigger infrastructure
anywhere, and standing one up for an occasional, non-critical, per-person prose memo would be a
second event-driven system to operate for a bonus checkbox. The Story Director's tick already visits
every live event on a 2-minute cadence (spec 05 §1) and already tolerates a slow or failed model
call without failing the tick (HANDOFF §4.23's discipline) — reusing it costs one aggregate `.count()`
per person and, on the rare tick where someone crosses a threshold, one Gemma call.

**Why it is not inside the tick's guardrail budget.** A delayed taste memo costs nothing downstream —
spec 07 §3's ranking would read `tasteProfile`/`tasteMemo` directly, and this module can update either
at any time — so a slow memo cycle must never make the director's real guardrails (the bounty budget,
the coverage ledger) wait on it. Note the conditional: **that ranking is not built.** Nothing reads
either field today outside `api/identity.py`'s deletion/export path, so this module's output is
currently write-only, which is a deliberate ordering (the cheap deterministic half exists and is
verified; the consumer is spec 07's unbuilt half) and not an oversight to be discovered later. It runs after the director and the publisher nudge in
`api/internal.py::_do_work`, and its own failures are caught there too.

**Where it writes, and why that is not the person document.** `tasteProfile`, `tasteMemo`,
`tasteMemoAt` and `lastMemoReactionCount` live in `people/{personId}/private/profile`, which is
deny-all to every client in `firestore.rules`. `people/{personId}` itself has to stay readable by
every event member — the kiosk leaderboard reads display names off it and the Highlights re-rank
reads `tier` — and a rule cannot grant one field of a document while withholding another. A
paragraph of prose about what a guest likes, sitting on a document the whole event can read, was the
single most personal thing exposed anywhere in this system.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi.concurrency import run_in_threadpool
from google.adk.agents import LlmAgent

from services import gemini
from services.armor_plugin import ModelArmorPlugin
from shared import fs, log
from shared.settings import settings

from . import memory

#: Spec 07 §2, verbatim: "after every 15 new reactions".
MEMO_EVERY_N_REACTIONS = 15
#: Loved/hidden captions carried into the prompt as concrete exemplars, each side. Bounded so the
#: prompt stays cheap regardless of how many photos someone has reacted to.
EXEMPLAR_CAP = 6
MEMO_MAX_CHARS = 600


@dataclass
class MemoResult:
    person_id: str
    reaction_count: int
    memo: str = ""
    tags: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    error: str | None = None


# ---------------------------------------------------------------- the deterministic vector


def _gather(event_id: str, person_id: str) -> tuple[str, dict[str, float], list[str], list[str]]:
    """Blocking Firestore reads: display name, the tag-affinity vector, exemplar captions.

    Recomputed from the whole reactions collection rather than maintained incrementally — a
    person's reactions are at most a few hundred documents, this only runs once per 15 new ones,
    and a full recompute can never drift from what is actually on the documents the way an
    incrementally-updated counter can if a client write (spec 09 §3's one client write) is ever
    retried or a reaction toggled back and forth.
    """
    person_snap = fs.person_ref(event_id, person_id).get()
    person = person_snap.to_dict() or {} if person_snap.exists else {}
    name = str(person.get("displayName") or "this guest")

    raw_counts: dict[str, float] = {}
    loved: list[str] = []
    hidden: list[str] = []
    for snap in fs.reactions_col(event_id, person_id).stream():
        reaction = snap.to_dict() or {}
        verdict = reaction.get("verdict")
        if verdict not in ("love", "hide"):
            continue
        media_snap = fs.media_ref(event_id, snap.id).get()
        if not media_snap.exists:
            continue
        curator = (media_snap.to_dict() or {}).get("curator") or {}
        caption = str(curator.get("caption") or "")
        sign = 1.0 if verdict == "love" else -1.0
        for tag in curator.get("momentTags") or []:
            raw_counts[str(tag)] = raw_counts.get(str(tag), 0.0) + sign
        people_count = curator.get("peopleCountEstimate")
        if isinstance(people_count, (int, float)):
            comp = "solo" if people_count <= 1 else ("pair" if people_count == 2 else "group")
            raw_counts[comp] = raw_counts.get(comp, 0.0) + sign
        if verdict == "love" and caption and len(loved) < EXEMPLAR_CAP:
            loved.append(caption[:200])
        elif verdict == "hide" and caption and len(hidden) < EXEMPLAR_CAP:
            hidden.append(caption[:200])

    peak = max((abs(v) for v in raw_counts.values()), default=0.0)
    weights = {k: round(v / peak, 3) for k, v in raw_counts.items()} if peak else {}
    return name, weights, loved, hidden


def _persist(event_id: str, person_id: str, *, affinity: dict[str, float], memo: str, reaction_count: int) -> None:
    """Writes to `people/{personId}/private/profile`, **not** to the person document.

    A memo about what someone likes, and the vector behind it, is the most personal text this system
    holds — and `people/{personId}` is readable by every member of the event, because the kiosk
    leaderboard needs display names and the Highlights re-rank needs `tier`. Firestore cannot withhold
    one field of a granted document, so the only way to keep this private is to put it somewhere no
    client rule grants at all: the deny-all `private/` subcollection (`shared/fs.py::person_private_ref`).
    """
    fs.person_private_ref(event_id, person_id).set(
        {
            "tasteProfile": affinity,
            **({"tasteMemo": memo, "tasteMemoAt": fs.SERVER_TIMESTAMP} if memo else {}),
            "lastMemoReactionCount": reaction_count,
        },
        merge=True,
    )


def _count_reactions(event_id: str, person_id: str) -> int:
    try:
        result = fs.reactions_col(event_id, person_id).count().get()
        return int(result[0][0].value)
    except Exception as exc:  # noqa: BLE001 - a count is telemetry, never a reason to fail a tick
        log.warn("taste_reaction_count_failed", event_id=event_id, person_id=person_id, err=str(exc))
        return 0


def _pending(event_id: str) -> list[tuple[str, int]]:
    """`[(personId, reactionCount)]` for everyone who just crossed another multiple of 15.

    One aggregate `.count()` per person — billed per 1,000 index entries, not per document — so this
    stays cheap at a few hundred reactions per person and dozens of people (same shape as
    `directors/story/ledger.py::_count`).
    """
    due: list[tuple[str, int]] = []
    for snap in fs.people_col(event_id).stream():
        # `lastMemoReactionCount` moved into `private/profile` along with the memo it watermarks, so
        # this is one extra document read per person per tick. A person with no memo yet has no
        # private document at all, which reads as 0 and is exactly right.
        private = fs.person_private_ref(event_id, snap.id).get().to_dict() or {}
        last = int(private.get("lastMemoReactionCount", 0) or 0)
        count = _count_reactions(event_id, snap.id)
        if count - last >= MEMO_EVERY_N_REACTIONS:
            due.append((snap.id, count))
    return due


# ---------------------------------------------------------------- the model call


def _memo_agent() -> LlmAgent:
    return LlmAgent(
        name="taste_memo_writer",
        model=gemini.adk_model(settings().model_taste_memo),
        instruction=(
            "You write a short, explainable taste memo for one wedding guest's private photo "
            "album, from data the caller already computed — you invent no facts beyond it. Input "
            "is a tag-affinity vector (positive = loved, negative = hidden, both normalised to "
            "[-1, 1]) plus a few example captions of photos this person loved and hid. Write "
            "EXACTLY three sentences, third person, plain prose, no markdown, no preamble: what "
            "they seem to love, what they seem to avoid, and one concrete pattern grounded in the "
            "examples. If the vector carries no clear signal yet, say plainly that there is not "
            "enough data yet — do not guess."
        ),
    )


def _prompt(name: str, affinity: dict[str, float], loved: list[str], hidden: list[str]) -> str:
    ranked = sorted(affinity.items(), key=lambda kv: -kv[1])
    return "\n".join(
        [
            f"Guest: {name}",
            f"Tag-affinity vector (loved minus hidden, normalised): {ranked}",
            f"Example loved-photo captions: {loved or '(none yet)'}",
            f"Example hidden-photo captions: {hidden or '(none yet)'}",
        ]
    )


async def write_memo_for(event_id: str, person_id: str, *, reaction_count: int) -> MemoResult:
    """Compute the vector, call Gemma, persist to Firestore + Memory Bank. Never raises."""
    name, affinity, loved, hidden = await run_in_threadpool(_gather, event_id, person_id)
    result = MemoResult(person_id=person_id, reaction_count=reaction_count, tags=len(affinity))

    try:
        text, usage = await gemini.run_text(
            _memo_agent(),
            [gemini.as_text_part(_prompt(name, affinity, loved, hidden))],
            stage="taste_memo",
            # The prompt carries Curator captions — guest-photo-derived text that entered the system
            # by a route other than this call — so it gets the same guard every director prompt does
            # (`directors/story/director.py`'s reasoning for the same plugin).
            plugins=[ModelArmorPlugin(surface="taste_memo", event_id=event_id)],
        )
        result.memo = text[:MEMO_MAX_CHARS]
        result.tokens_in, result.tokens_out = usage.tokensIn, usage.tokensOut
    except gemini.ModelError as exc:
        # Off the critical path by design (spec 07 §2): a malformed or refused memo simply skips this
        # cycle, exactly like a Curator caption never gates a photo's `status`. The vector still gets
        # persisted below — it is deterministic and owes the model nothing.
        result.error = str(exc)[:300]
        result.tokens_in, result.tokens_out = exc.usage.tokensIn, exc.usage.tokensOut
        log.warn("taste_memo_failed", event_id=event_id, person_id=person_id, err=result.error)

    await run_in_threadpool(
        _persist, event_id, person_id, affinity=affinity, memo=result.memo, reaction_count=reaction_count
    )
    if result.memo:
        await memory.remember_taste_memo(event_id, person_id, result.memo)

    log.line(
        "taste_memo",
        event_id=event_id,
        person_id=person_id,
        reactions=reaction_count,
        tags=result.tags,
        tokens_in=result.tokens_in or None,
        tokens_out=result.tokens_out or None,
        err=result.error,
    )
    return result


async def run_pending(event_id: str) -> list[MemoResult]:
    """Called from the tick (`api/internal.py::_do_work`). Bounded, best-effort.

    Never raises past a log line: one person's Gemma call failing must not skip the rest, and the
    whole cycle failing must not touch the director's report or the wall refresh that already ran.
    """
    results: list[MemoResult] = []
    try:
        candidates = await run_in_threadpool(_pending, event_id)
    except Exception as exc:  # noqa: BLE001 - a listing failure must not fail the tick
        log.warn("taste_memo_scan_failed", event_id=event_id, err=str(exc))
        return results
    for person_id, count in candidates:
        try:
            results.append(await write_memo_for(event_id, person_id, reaction_count=count))
        except Exception as exc:  # noqa: BLE001 - one person's failure must not skip the rest
            log.warn("taste_memo_person_failed", event_id=event_id, person_id=person_id, err=str(exc))
    return results
