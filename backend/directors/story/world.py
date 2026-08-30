"""The world model — what the event's photographs say about the physical place it happens in.

The question that produced this module: at a wedding, a guest uploads a photo of a hike, and it
reaches the public gallery and the kiosk wall. Why? Because `shared/visibility.py::decide` has six
inputs — consent, the Guardian's verdict, the aesthetic floor, subject vetoes, the panic freeze,
deletion — and **not one of them asks whether a photo is about the event.** There is no notion of
topicality anywhere in the system, and that is a design gap rather than an oversight to be patched
with a rule, because every obvious rule is wrong:

- "Landscapes are off-topic" breaks at a hill-station wedding, where the valley *is* the venue.
- No per-photo classifier can separate "the valley from the terrace, 45 minutes before the ceremony"
  from "the same guest's hike 2 km away that morning". They are visually identical, and the
  distinguishing information is not in the pixels. The Curator is explicitly forbidden from inferring
  context anyway (`workers/curate/agent.py`: *"never infer the stage from time of day or lighting"*).

**The signal is not in the photo. It is in the distribution of the other photos.** At a ballroom
wedding `outdoor_nature` is ~0.5% of the corpus and a hike is an outlier; at a hill station it is ~70%
and the hike is unremarkable. The system calibrates itself to the venue by observing it, so there is
nothing for a host to mis-declare and no cultural assumption to get wrong.

## Two layers, and why the split is the whole design

**Layer 1 — the counts, in Firestore, exact.** `sceneSetting → count` per stage, incremented by
`shared/coverage.py::bump` inside the transaction that already flips `status='indexed'`. Exactly-once
by construction (`pipeline._derive_status` returns an update once per item, ever), write-only, and
contention-free. **This is the only layer any decision reads** — `publisher/program.py`'s `onTopic`
term consumes it, arithmetically, in a pure function.

**Layer 2 — the prose, here.** Three sentences describing the venue, written by Gemma from Layer 1's
counts, stored on `ledger/worldModel`, mirrored best-effort into Memory Bank. Read by the Story
Director's prompt so it can say *where* to go, and by nothing that decides anything.

**Layer 2 is generated from Layer 1**, which is what makes it accountable: every sentence traces back
to a number, and the counts are auditable on the coverage shards. This is exactly the split
`taste.py` already draws — a deterministic affinity vector, then a Gemma memo written from it, with
the memo gating nothing.

## Why the counts are not in Memory Bank

The obvious instinct is to put all of this in Memory Bank, since "an accumulated understanding of the
event" is what a memory store is for. Three reasons not to, and the first is a hard stop:

1. `directors/story/memory.py` states spec 11 §4's mandate verbatim — memory holds narrative context
   that *"must never be able to decide an outcome"*, because *"a probabilistic store that could raise
   a bounty's payout or widen an exposure would be agent memory quietly acquiring product-critical
   authority."* A relevance signal that changes what the wall shows is exactly that.
2. `memory.py::_from_memory_bank` swallows every exception and returns `""`. Correct for soft context
   — but anything *gating* on it would silently change behaviour across a whole event on one warning
   log. The store is designed to degrade to nothing, so nothing may depend on it.
3. It is the wrong data structure. This needs exact aggregate counts; Memory Bank is semantic recall
   over prose. Asking it "what fraction of photos are indoors" returns a plausible paragraph, and a
   differently plausible one on the next call.

So Memory Bank keeps doing what it does — the host's free-text standing preferences — and gains the
distilled venue paragraph, which is genuinely narrative and genuinely gates nothing.

## Keeping it warm

Two things, and neither is a cache invalidation problem. The distillation runs on the director tick,
so the prose is never more than `WORLD_MODEL_EVERY_N_PHOTOS` photos stale. And every consumer reads
the **stored** prose — one document read, no model call on any critical path. The expensive step is
amortised across 25 photos; the read is free.

Mechanically this module is `taste.py`'s skeleton with different inputs: a watermark-delta gate, a
full deterministic recompute, a cheap-model prose pass that is allowed to fail, and a persist that
writes the deterministic half unconditionally.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fastapi.concurrency import run_in_threadpool
from google.adk.agents import LlmAgent

from schemas.common import UNINFORMATIVE_SETTINGS
from services import gemini
from services.armor_plugin import ModelArmorPlugin
from shared import coverage, fs, log
from shared.settings import settings

from . import memory

#: How many newly-indexed photos it takes to redistil. A **watermark delta**, not a modulo, for the
#: same reason `taste.py::MEMO_EVERY_N_REACTIONS` is: a backlog of 60 photos should fire once, not
#: twice. NOT spec-pinned (HANDOFF §9) — 25 is enough new evidence to move a share meaningfully, and
#: at a 2,000-photo wedding it is ~80 calls to a free-tier model across the whole day.
WORLD_MODEL_EVERY_N_PHOTOS = 25

#: Hard trim before storage and before any prompt. Three sentences about a venue do not need more,
#: and this text is interpolated into the Story Director's prompt on every tick — an unbounded field
#: on a document is an unbounded prompt, the same reasoning `memory.MAX_PREFERENCE_CHARS` carries.
PROSE_MAX_CHARS = 400

#: How many stages the prompt describes. The prompt is per-tick cost; a wedding has 4-6 stages and an
#: event with thirty is one where the per-stage detail has stopped being informative anyway.
MAX_STAGES_IN_PROMPT = 8


@dataclass
class WorldSnapshot:
    """Layer 1, read and shaped. Pure data — no model has touched any of it.

    Shaped for *this* module's prompt-building and prose-triggering, not for the kiosk ranking:
    `publisher/program.py`'s `onTopic` term reads the same underlying counts through its own
    `SceneContext`, built independently by `publisher/store.py::scene_context` straight from
    `shared/coverage.py`. Deliberately two dataclasses rather than one shared here — `program.py` is
    the kiosk's pure ranking core and must not import a director-agent module, so the two sides of the
    world model's hard layer stay decoupled even though they describe the same photographs.
    """

    #: Event-wide `sceneSetting → count`, summed across shards (`coverage.scene_totals`).
    scenes: dict[str, int] = field(default_factory=dict)
    #: `stageId → {sceneSetting: count}`. The more useful view: an event is a *sequence* of settings,
    #: so an event-wide count reads every stage change as an anomaly.
    by_stage: dict[str, dict[str, int]] = field(default_factory=dict)
    #: `stageId → the most-observed informative setting`, or absent when a stage has none.
    dominant: dict[str, str] = field(default_factory=dict)
    #: Total photos counted — the watermark, and the `WORLD_MIN_CORPUS` gate's input.
    total: int = 0
    #: Photos whose setting carries no location information (`closeup_detail`, `unknown`). Tracked
    #: separately because they must never be read as evidence *against* a photo; a stage that is all
    #: ring shots has told us nothing about where it happened, which is different from being unusual.
    uninformative: int = 0

    @property
    def informative_total(self) -> int:
        return max(0, self.total - self.uninformative)

    def share(self, setting: str) -> float:
        """This setting's share of the *informative* corpus.

        Denominated on informative photos only, so a stage full of close-ups does not make every real
        setting look rare by diluting the denominator.
        """
        denominator = self.informative_total
        if denominator <= 0:
            return 0.0
        return self.scenes.get(setting, 0) / denominator


def snapshot(shards: dict[str, coverage.StageCoverage]) -> WorldSnapshot:
    """Layer 1 → `WorldSnapshot`. Pure, so it is checkable with no Firestore and no spend.

    Takes shards rather than an eventId because the caller already has them: the director's LEDGER
    step reads `coverage.read()` every tick, and re-reading here would double that cost for data that
    has not changed since a moment ago.
    """
    totals = coverage.scene_totals(shards)
    by_stage = {
        stage_id: dict(shard.scenes) for stage_id, shard in shards.items() if shard.scenes
    }
    dominant = {
        stage_id: shard.dominant_scene
        for stage_id, shard in shards.items()
        if shard.dominant_scene
    }
    return WorldSnapshot(
        scenes=totals,
        by_stage=by_stage,
        dominant={k: v for k, v in dominant.items() if v},
        total=sum(totals.values()),
        uninformative=sum(totals.get(tag, 0) for tag in UNINFORMATIVE_SETTINGS),
    )


# ---------------------------------------------------------------- the model call


def _prose_agent() -> LlmAgent:
    """Gemma, no output schema, three sentences. Same shape and same model as the taste memo.

    A free-tier model off the critical path, deliberately: this text explains a ranking and steers a
    prompt, and if it never arrives the numbers it would have described are still there and still
    driving every decision.
    """
    return LlmAgent(
        name="world_model_writer",
        model=gemini.adk_model(settings().model_taste_memo),
        instruction=(
            "You describe the physical setting of a live event, for another agent to reason with. "
            "The input is a count of scene settings observed across the event's photographs, broken "
            "down by stage — you have not seen any photograph and you invent nothing beyond these "
            "counts. Write AT MOST three sentences of plain prose, no markdown, no preamble: where "
            "this event appears to be held, which settings host which parts of it, and anything "
            "notably consistent. Name no venue, town, landmark, weather or person — the counts do "
            "not contain them, so neither do you. If the counts show no clear pattern, say exactly "
            "that in one sentence instead of guessing."
        ),
    )


def _prompt(event: dict[str, Any], snap: WorldSnapshot) -> str:
    """Counts in, nothing else. No captions, no mediaIds, no names.

    Worth stating what is deliberately absent: the Curator's captions would make richer prose and are
    exactly the wrong input here. They are per-photo descriptions of *people*, and a paragraph
    synthesised from them would be a paragraph about guests rather than about a place — traceable to
    nothing countable, and carrying personal detail into a field that is read into another prompt.
    """
    lines = [
        f"Event: {event.get('name') or 'unnamed'}",
        f"Photographs counted: {snap.total} ({snap.uninformative} show no setting at all)",
        "",
        "Settings observed across the whole event (count, share of those that show a setting):",
    ]
    ranked = sorted(snap.scenes.items(), key=lambda kv: -kv[1])
    for setting, count in ranked:
        if setting in UNINFORMATIVE_SETTINGS:
            continue
        lines.append(f"  {setting}: {count} ({snap.share(setting) * 100:.0f}%)")
    if not any(s not in UNINFORMATIVE_SETTINGS for s in snap.scenes):
        lines.append("  (none — every photograph so far shows no identifiable setting)")

    labels = {
        str(stage.get("stageId")): str(stage.get("label") or stage.get("stageId"))
        for stage in (event.get("stages") or [])
        if stage.get("stageId")
    }
    lines += ["", "By stage:"]
    shown = 0
    for stage_id, scenes in snap.by_stage.items():
        if shown >= MAX_STAGES_IN_PROMPT:
            break
        informative = {t: n for t, n in scenes.items() if t not in UNINFORMATIVE_SETTINGS}
        if not informative:
            continue
        detail = ", ".join(f"{t} {n}" for t, n in sorted(informative.items(), key=lambda kv: -kv[1]))
        label = labels.get(stage_id, stage_id)
        lines.append(f"  {label} ({stage_id}): {detail}")
        shown += 1
    if shown == 0:
        lines.append("  (no stage has an identifiable setting yet)")
    return "\n".join(lines)


# ---------------------------------------------------------------- persistence


def _stored(event_id: str) -> dict[str, Any]:
    snap = fs.world_model_ref(event_id).get()
    return (snap.to_dict() or {}) if snap.exists else {}


def _persist(event_id: str, snap: WorldSnapshot, prose: str) -> None:
    """Layer 1's shaped view unconditionally; the prose only if there is prose.

    The conditional spread is `taste.py::_persist`'s trick and it matters: a failed model call must not
    blank a good paragraph from ten minutes ago. The watermark advances either way, so a persistently
    failing model costs one distillation attempt per 25 photos rather than one per tick.
    """
    fs.world_model_ref(event_id).set(
        {
            "scenes": snap.scenes,
            "byStage": snap.by_stage,
            "dominant": snap.dominant,
            "total": snap.total,
            "uninformative": snap.uninformative,
            **({"prose": prose, "proseAt": fs.SERVER_TIMESTAMP} if prose else {}),
            "watermark": snap.total,
            "updatedAt": fs.SERVER_TIMESTAMP,
        },
        merge=True,
    )


def recall_prose(event_id: str) -> str:
    """The stored paragraph, for a prompt. One document read, no model call — this is "warm".

    Never raises: an unreadable world model must degrade to "we have not characterised this venue",
    exactly as an unreachable Memory Bank degrades to "the host has no standing preferences".
    """
    try:
        return str(_stored(event_id).get("prose") or "")[:PROSE_MAX_CHARS]
    except Exception as exc:  # noqa: BLE001 - soft context, see docstring
        log.warn("world_model_read_failed", event_id=event_id, err=str(exc))
        return ""


# ---------------------------------------------------------------- the cycle


async def run_if_due(
    event_id: str,
    event: dict[str, Any],
    shards: dict[str, coverage.StageCoverage],
) -> WorldSnapshot | None:
    """Redistil if enough new photos have landed. Returns the snapshot it wrote, or None.

    Called last in `api/internal.py::_do_work`, beside `taste.run_pending` and for the same reason: a
    slow or failed distillation must never make the director's real guardrails — the bounty budget,
    the coverage ledger — wait on it. Never raises.
    """
    try:
        snap = snapshot(shards)
        stored = await run_in_threadpool(_stored, event_id)
        watermark = int(stored.get("watermark") or 0)

        if snap.total - watermark < WORLD_MODEL_EVERY_N_PHOTOS:
            return None
        if snap.informative_total <= 0:
            # Every photo so far is a close-up or unreadable. Advance the watermark so this does not
            # re-evaluate on every tick, but write no prose — there is genuinely nothing to say, and
            # a model asked to describe a place from zero evidence will describe one anyway.
            await run_in_threadpool(_persist, event_id, snap, "")
            log.line("world_model", event_id=event_id, total=snap.total, note="no informative settings")
            return snap

        prose, error = "", None
        tokens_in = tokens_out = 0
        try:
            text, usage = await gemini.run_text(
                _prose_agent(),
                [gemini.as_text_part(_prompt(event, snap))],
                stage="world_model",
                # Counts and host-authored stage labels. The labels are host free text that entered by
                # another route, which is the same argument every other director-adjacent prompt makes
                # for this plugin.
                plugins=[ModelArmorPlugin(surface="world_model", event_id=event_id)],
            )
            prose = text.strip()[:PROSE_MAX_CHARS]
            tokens_in, tokens_out = usage.tokensIn, usage.tokensOut
        except gemini.ModelError as exc:
            # The deterministic half owes the model nothing and is persisted below regardless.
            error = str(exc)[:300]
            tokens_in, tokens_out = exc.usage.tokensIn, exc.usage.tokensOut
            log.warn("world_model_prose_failed", event_id=event_id, err=error)

        await run_in_threadpool(_persist, event_id, snap, prose)
        if prose:
            await memory.remember_world_model(event_id, prose)

        log.line(
            "world_model",
            event_id=event_id,
            total=snap.total,
            settings=len(snap.scenes),
            stages=len(snap.by_stage),
            tokens_in=tokens_in or None,
            tokens_out=tokens_out or None,
            err=error,
        )
        return snap
    except Exception as exc:  # noqa: BLE001 - must never fail the tick
        log.warn("world_model_failed", event_id=event_id, err=str(exc))
        return None
