"""The director's tick-to-tick working memory, and the answer to context rot.

Spec 05 §1: the session "carries tick-to-tick memory: issued bounties, deferred ideas, last
assessment — so the LLM reasons over a narrative, not a cold start", bounded by "a rolling window of
the last 10 tick summaries, with older ticks compacted into a single paragraph (compaction happens in
the deterministic Act step, not by the LLM)."

Three decisions here are load-bearing:

- **It lives in Firestore, not in a session cache.** HANDOFF §4.18: this document holds the last stage
  the director saw (which is what arms a new stage's bounties exactly once), the deferred reel
  commissions, and the permanent coverage gaps the wrap report has to be honest about. All three are
  read inside transactions, are host-visible evidence, and must survive a redeploy. That is a system
  of record. Sessions are for what a conversation needs to remember; this is what the *event* needs
  to remember.
- **Compaction is arithmetic, not summarization.** The overflowing ticks are folded into a counted
  sentence — how many ticks, how many bounties issued, how many fulfilled, which stages passed — by
  code. Asking a model to summarize its own history is how a long-running agent's context slowly
  becomes a story about itself: every compaction is a lossy re-telling of the last compaction, and
  after fifty ticks the numbers are vibes. Counting cannot drift.
- **Recording is idempotent per tickId.** A host pressing "run director now" twice, or a Cloud Tasks
  re-delivery of an interleaved tick, must not append the same tick to the window twice. The tick
  lease already makes concurrent ticks impossible; this makes a *replayed* one harmless.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from typing import Any

from shared import fs, log
from shared.settings import DIRECTOR_SESSION_WINDOW

#: How many characters of an assessment survive into the window. Long enough to be a narrative,
#: short enough that ten of them plus a ledger stay well inside a sane prompt.
MAX_ASSESSMENT_CHARS = 320

#: Permanent gaps and deferred commissions are kept for the wrap report, not forever.
MAX_PERMANENT_GAPS = 40
MAX_COMMISSIONS = 20


@dataclass
class TickSummary:
    """One line of the director's own history."""

    tickId: str
    at: dt.datetime | None = None
    assessment: str = ""
    actions: list[str] = field(default_factory=list)
    issued: int = 0
    fulfilled: int = 0
    expired: int = 0
    stageId: str | None = None
    #: The tick's event-local stamp ("Day 2 Tue 14:05"), computed at record time by the calendar
    #: (spec 13) — stored, because a window of ten lines spanning three days is unreadable as bare
    #: %H:%M, and the model's own history is the one place it can learn that a day has passed.
    day: str = ""

    def as_line(self) -> str:
        when = self.day or (self.at.strftime("%H:%M") if isinstance(self.at, dt.datetime) else "--:--")
        acts = ", ".join(self.actions) if self.actions else "NO_OP"
        return f"[{when} stage={self.stageId or '-'}] {acts} :: {self.assessment or '(no note)'}"

    def to_doc(self) -> dict[str, Any]:
        return {
            "tickId": self.tickId,
            "at": self.at,
            "assessment": self.assessment[:MAX_ASSESSMENT_CHARS],
            "actions": self.actions,
            "issued": self.issued,
            "fulfilled": self.fulfilled,
            "expired": self.expired,
            "stageId": self.stageId,
            "day": self.day,
        }

    @classmethod
    def from_doc(cls, doc: dict[str, Any]) -> "TickSummary":
        return cls(
            tickId=str(doc.get("tickId") or ""),
            at=doc.get("at") if isinstance(doc.get("at"), dt.datetime) else None,
            assessment=str(doc.get("assessment") or ""),
            actions=[str(a) for a in (doc.get("actions") or [])],
            issued=int(doc.get("issued") or 0),
            fulfilled=int(doc.get("fulfilled") or 0),
            expired=int(doc.get("expired") or 0),
            stageId=doc.get("stageId"),
            day=str(doc.get("day") or ""),
        )


@dataclass
class DirectorState:
    """`ledger/directorState`, as the tick sees it."""

    tick_count: int = 0
    last_tick_id: str | None = None
    last_stage_id: str | None = None
    compacted: str = ""
    ticks: list[TickSummary] = field(default_factory=list)
    commissions: list[dict[str, Any]] = field(default_factory=list)
    permanent_gaps: list[dict[str, Any]] = field(default_factory=list)
    #: The drift signal's target on the last tick and how many consecutive ticks it has held —
    #: the evidence half of spec 13's advance rule (`act._decide_advance`). State, not memory:
    #: two ticks have to agree, so one of them has to remember the other.
    drift_stage_id: str | None = None
    drift_streak: int = 0
    #: Stages whose lapse has already been archived into `permanent_gaps` — the "exactly once" in
    #: the gap lifecycle. Ids, not records: the records live in `permanent_gaps` beside the
    #: expired-bounty ones the wrap report reads.
    archived_stage_ids: list[str] = field(default_factory=list)

    def narrative(self) -> str:
        """What the REASON step reads as its own memory: the compaction, then the window."""
        lines: list[str] = []
        if self.compacted:
            lines.append(f"Earlier: {self.compacted}")
        for summary in self.ticks:
            lines.append(summary.as_line())
        return "\n".join(lines) if lines else "(this is the first tick of this event)"


def load(event_id: str) -> DirectorState:
    snap = fs.director_state_ref(event_id).get()
    doc = (snap.to_dict() or {}) if snap.exists else {}
    return DirectorState(
        tick_count=int(doc.get("tickCount") or 0),
        last_tick_id=doc.get("lastTickId"),
        last_stage_id=doc.get("lastStageId"),
        compacted=str(doc.get("compacted") or ""),
        ticks=[TickSummary.from_doc(t) for t in (doc.get("ticks") or []) if isinstance(t, dict)],
        commissions=[c for c in (doc.get("commissions") or []) if isinstance(c, dict)],
        permanent_gaps=[g for g in (doc.get("permanentGaps") or []) if isinstance(g, dict)],
        drift_stage_id=doc.get("driftStageId"),
        drift_streak=int(doc.get("driftStreak") or 0),
        archived_stage_ids=[str(s) for s in (doc.get("archivedStages") or [])],
    )


def record(
    event_id: str,
    state: DirectorState,
    summary: TickSummary,
    *,
    stage_id: str | None,
    commissions: list[dict[str, Any]] | None = None,
    permanent_gaps: list[dict[str, Any]] | None = None,
    drift: tuple[str | None, int] | None = None,
    archived_stage_ids: list[str] | None = None,
) -> DirectorState:
    """Append this tick to the window, compact the overflow, and store it. Idempotent per tickId."""
    if state.last_tick_id == summary.tickId:
        log.info("director_session_replay_ignored", event_id=event_id, tick_id=summary.tickId)
        return state

    window = [*state.ticks, summary]
    overflow, window = window[:-DIRECTOR_SESSION_WINDOW], window[-DIRECTOR_SESSION_WINDOW:]
    compacted = _compact(state.compacted, overflow)

    state.ticks = window
    state.compacted = compacted
    state.tick_count += 1
    state.last_tick_id = summary.tickId
    state.last_stage_id = stage_id
    if commissions:
        state.commissions = (state.commissions + commissions)[-MAX_COMMISSIONS:]
    if permanent_gaps:
        state.permanent_gaps = (state.permanent_gaps + permanent_gaps)[-MAX_PERMANENT_GAPS:]
    if drift is not None:
        state.drift_stage_id, state.drift_streak = drift
    if archived_stage_ids:
        merged = [*state.archived_stage_ids, *archived_stage_ids]
        state.archived_stage_ids = list(dict.fromkeys(merged))  # de-dup, order kept

    fs.director_state_ref(event_id).set(
        {
            "tickCount": state.tick_count,
            "lastTickId": state.last_tick_id,
            "lastTickAt": fs.SERVER_TIMESTAMP,
            "lastStageId": state.last_stage_id,
            "lastAssessment": summary.assessment[:MAX_ASSESSMENT_CHARS],
            "compacted": state.compacted,
            "ticks": [t.to_doc() for t in state.ticks],
            "commissions": state.commissions,
            "permanentGaps": state.permanent_gaps,
            "driftStageId": state.drift_stage_id,
            "driftStreak": state.drift_streak,
            "archivedStages": state.archived_stage_ids,
            "updatedAt": fs.SERVER_TIMESTAMP,
        },
        merge=True,
    )
    return state


def _compact(existing: str, overflow: list[TickSummary]) -> str:
    """Fold ticks falling out of the window into one counted sentence. No model involved.

    The previous compaction's own numbers are parsed back out and added to, so the sentence stays one
    sentence however long the event runs — a five-hour wedding at a 2-minute cadence is 150 ticks, and
    this is what keeps the prompt the same size on tick 150 as on tick 11.
    """
    if not overflow:
        return existing
    base = _parse_compaction(existing)
    base["ticks"] += len(overflow)
    for tick in overflow:
        base["issued"] += tick.issued
        base["fulfilled"] += tick.fulfilled
        base["expired"] += tick.expired
        if tick.stageId and tick.stageId not in base["stages"]:
            base["stages"].append(tick.stageId)

    stages = " → ".join(base["stages"][-6:]) or "none recorded"
    return (
        f"{base['ticks']} earlier ticks; {base['issued']} bounties issued, "
        f"{base['fulfilled']} fulfilled, {base['expired']} expired; stages seen: {stages}."
    )


def _parse_compaction(text: str) -> dict[str, Any]:
    """Read back the four numbers `_compact` writes, so compaction is additive rather than lossy."""
    out: dict[str, Any] = {"ticks": 0, "issued": 0, "fulfilled": 0, "expired": 0, "stages": []}
    if not text:
        return out
    for key, pattern in (
        ("ticks", r"(\d+) earlier ticks"),
        ("issued", r"(\d+) bounties issued"),
        ("fulfilled", r"(\d+) fulfilled"),
        ("expired", r"(\d+) expired"),
    ):
        match = re.search(pattern, text)
        if match:
            out[key] = int(match.group(1))
    stages = re.search(r"stages seen: (.+?)\.?$", text)
    if stages and stages.group(1) != "none recorded":
        out["stages"] = [s.strip() for s in stages.group(1).split("→") if s.strip()]
    return out


def note_stage(event_id: str, stage_id: str | None) -> None:
    """Remember the stage the director has already armed, outside the tick's own record.

    Called by `act.arm_stage_moments` the moment the arming succeeds, rather than at the end of the
    tick, because the arming is the expensive half: a tick that armed four bounties and then crashed
    before recording must not arm them again on the next pass (`bounties` dedupe would catch it, but
    relying on a second guard for a first guard's job is how a duplicate eventually gets through).
    """
    fs.director_state_ref(event_id).set(
        {"lastStageId": stage_id, "lastStageArmedAt": fs.SERVER_TIMESTAMP},
        merge=True,
    )


def remember_commission(entry: dict[str, Any]) -> dict[str, Any]:
    """Stamp a deferred reel commission so the wrap report and S11 can both read it."""
    return {**entry, "at": dt.datetime.now(dt.timezone.utc)}


#: Kept next to the writers so a reader of this module sees every field the document can hold.
DOC_FIELDS = (
    "tickCount",
    "lastTickId",
    "lastTickAt",
    "lastStageId",
    "lastStageArmedAt",
    "lastAssessment",
    "compacted",
    "ticks",
    "commissions",
    "permanentGaps",
    "driftStageId",
    "driftStreak",
    "archivedStages",
    "updatedAt",
)
