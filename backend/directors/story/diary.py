"""The Event Diary — a qualitative memo per closed stage (spec 13 §8).

The counters know *how much* was photographed; nothing before this knew *what it felt like*. When
a stage lapses (the same transition the archive step detects), one flash-lite call distills its
stored Curator outputs — captions, moment tags, scene counts, people mix — into a two/three-
sentence memo: "mostly neon street shots and food close-ups, the group playful, one strong group
shot at the crossing." That is what the recap film's brief, the wrap headline and the director's
bounty copy get to know about a chapter that already ended.

The binding boundary (HANDOFF §4.18, spec 13 §8): **the diary flavors creative surfaces only.**
Nothing here is read by ranking, visibility or the award path, and nothing here may ever be — the
Firestore mirror lives in `ledger/` (host-readable, client-side rules already cover it) purely so
the system of record is queryable and the Memory Bank write can stay the best-effort mirror every
other soft write in this package is. A failed distillation is logged and skipped; a tick never
waits on a diary.
"""

from __future__ import annotations

import functools
from typing import Any

from google.adk.agents import LlmAgent
from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter
from google.genai import types
from pydantic import BaseModel, Field

from schemas.common import MediaStatus
from services import gemini
from shared import coverage, fs, log
from shared.settings import settings

from . import ledger as ledger_mod, memory

STAGE = "diary"

#: How many of the stage's best captions ride into the prompt. The counts carry the shape; a few
#: captions carry the texture. More is prompt weight for a memo capped at three sentences.
_CAPTION_SAMPLE = 8

_DOC_PREFIX = "diary_"


class DiaryOut(BaseModel):
    memo: str = Field(default="", max_length=600)


INSTRUCTION = """\
You write the diary line for one chapter of a live event, from data another agent already
computed — you invent nothing beyond it. Input: the chapter's name, its coverage counts (moments
seen, scene settings, how many photos and how many good ones, group sizes in frame) and a few
captions of its best photographs.

Write TWO or THREE sentences, plain prose, no markdown, no preamble: what this chapter looked and
felt like, what people were doing, and the one thing a film editor should know about it (a
standout moment, or an honest note that coverage was thin). Warm, concrete, grounded only in the
input. Never name a person the input did not name.
"""


@functools.lru_cache(maxsize=1)
def _agent() -> LlmAgent:
    return LlmAgent(
        name="diary_writer",
        description="Distills one closed stage's coverage into a short qualitative memo.",
        model=gemini.adk_model(settings().model_classifier),
        instruction=INSTRUCTION,
        output_schema=DiaryOut,
        output_key="diary",
        generate_content_config=types.GenerateContentConfig(temperature=0.3),
    )


def _captions_for_stage(event_id: str, stage_id: str) -> list[str]:
    """Best captions of one stage, off the reel selector's existing composite index
    (`visibility, status, curator.aestheticScore desc`) — filtered in Python, no new index."""
    found: list[str] = []
    try:
        query = (
            fs.media_col(event_id)
            .where(filter=FieldFilter("visibility", "in", ["public", "pool"]))
            .where(filter=FieldFilter("status", "==", MediaStatus.INDEXED.value))
            .order_by("curator.aestheticScore", direction=firestore.Query.DESCENDING)
            .limit(120)
        )
        for snap in query.stream():
            doc = snap.to_dict() or {}
            curator = doc.get("curator") or {}
            if str(curator.get("stageId") or "") != stage_id:
                continue
            caption = str(curator.get("caption") or "").strip()
            if caption:
                found.append(caption)
            if len(found) >= _CAPTION_SAMPLE:
                break
    except Exception as exc:  # noqa: BLE001 - captions are texture, not a requirement
        log.warn("diary_caption_query_failed", event_id=event_id, err=str(exc))
    return found


def _prompt(stage: ledger_mod.StageView, shard: coverage.StageCoverage, captions: list[str]) -> str:
    moments = ", ".join(f"{mid}×{n}" for mid, n in sorted(shard.moments.items())) or "none tagged"
    scenes = ", ".join(f"{s}×{n}" for s, n in sorted(shard.scenes.items())) or "unknown"
    groups = ", ".join(f"{b}×{n}" for b, n in sorted(shard.people_buckets.items())) or "no people data"
    lines = [
        f"CHAPTER: {stage.label} ({stage.stage_id})",
        f"photos={shard.photo_count} good={shard.highlight_count} meanAesthetic={shard.mean_aesthetic:.2f}",
        f"moments seen: {moments}",
        f"scene settings: {scenes}",
        f"group sizes in frame: {groups}",
        "best captions:",
    ]
    lines += [f"- {c}" for c in captions] or ["- (none)"]
    return "\n".join(lines)


async def write_for_lapsed(
    event_id: str, led: ledger_mod.Ledger, archived_ids: list[str]
) -> list[str]:
    """One memo per newly-lapsed stage. Returns the stageIds written. Never raises."""
    written: list[str] = []
    shards = {s.stage_id: s for s in led.stages}
    for stage_id in archived_ids:
        stage = shards.get(stage_id)
        if stage is None or stage.photo_count == 0:
            continue  # a chapter with zero photos has no texture to record; the gap record says why
        shard = coverage.StageCoverage(
            stage_id=stage_id,
            photo_count=stage.photo_count,
            highlight_count=stage.highlight_count,
            aesthetic_sum=stage.mean_aesthetic * stage.photo_count,
            moments=dict(stage.moment_counts),
        )
        # The StageView carries no scenes/buckets; read the real shard for the full picture.
        real = coverage.read(event_id).get(stage_id)
        if real is not None:
            shard = real
        try:
            captions = _captions_for_stage(event_id, stage_id)
            out, usage = await gemini.run_structured(
                _agent(),
                [gemini.as_text_part(_prompt(stage, shard, captions))],
                DiaryOut,
                stage=STAGE,
            )
            memo = (out.memo or "").strip()
            if not memo:
                continue
            fs.ledger_ref(event_id, f"{_DOC_PREFIX}{stage_id}").set(
                {
                    "stageId": stage_id,
                    "label": stage.label,
                    "memo": memo,
                    "writtenAt": fs.SERVER_TIMESTAMP,
                }
            )
            await memory.remember_diary(event_id, stage_id, memo)
            written.append(stage_id)
            log.line(
                "diary",
                event_id=event_id,
                stage=stage_id,
                chars=len(memo),
                tokens_in=usage.tokensIn or None,
            )
        except Exception as exc:  # noqa: BLE001 - the diary is off the critical path, always
            log.warn("diary_write_failed", event_id=event_id, stage=stage_id, err=str(exc))
    return written


def recall_all(event_id: str) -> dict[str, str]:
    """`stageId → memo`, from the Firestore mirrors. The read path every consumer uses — the
    Memory Bank copy is the soft mirror, never the source (HANDOFF §4.18)."""
    out: dict[str, str] = {}
    try:
        for snap in fs.event_ref(event_id).collection("ledger").stream():
            if not snap.id.startswith(_DOC_PREFIX):
                continue
            doc = snap.to_dict() or {}
            memo = str(doc.get("memo") or "").strip()
            if memo:
                out[str(doc.get("stageId") or snap.id[len(_DOC_PREFIX):])] = memo
    except Exception as exc:  # noqa: BLE001 - advisory prose; absence is a fine answer
        log.warn("diary_recall_failed", event_id=event_id, err=str(exc))
    return out
