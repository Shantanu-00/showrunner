"""Memory Bank access — the only soft store in the control plane, and the scope-key mandate.

Spec 11 §4 is the whole design in one line: **VIP is policy, not memory.** What lives here is the
host's free-text standing preferences ("keep the drone shots off the wall", "my mother-in-law hates
being photographed eating") — narrative context that should colour how the director reasons and must
never be able to decide an outcome. Tier, points, visibility and every guardrail in `act.py` are read
from Firestore, deterministically, because a probabilistic store that could raise a bounty's payout
or widen an exposure would be agent memory quietly acquiring product-critical authority.

Two properties this file exists to make checkable rather than merely stated:

- **Every scope key starts with the eventId.** `spec 11 §4`'s grep-auditable mandate — one reviewer
  running `grep -rn "def scope" backend/` finds two functions and can confirm both. `personId` is
  already a per-event ULID, so cross-event collision was already astronomically improbable; the
  prefix turns that accident of randomness into a guarantee. Showrunner never links person identity
  across events, and there is no shared key for it to leak through.
- **The absence of Memory Bank is not an error.** Until a Vertex AI Agent Engine resource exists
  (`AGENT_ENGINE_ID`), the host's preferences are read from `events/{eventId}.hostPreferences` — the
  same free text, the same scope, one hop shorter. The director's reasoning is identical either way,
  which is the honest way to say that this input was never load-bearing.
"""

from __future__ import annotations

from typing import Any

from shared import fs, log
from shared.settings import settings

#: Trimmed hard before it reaches a prompt. Host free text is untrusted input (it also passes the
#: Model Armor plugin on the way into the model), and an unbounded field on a document is an
#: unbounded prompt.
MAX_PREFERENCE_CHARS = 600


def scope(event_id: str) -> str:
    """The host scope: `{eventId}:host` (spec 11 §4, verbatim)."""
    return f"{event_id}:host"


def world_scope(event_id: str) -> str:
    """The event's own physical-setting scope: `{eventId}:world`.

    Still eventId-prefixed, so spec 11 §4's grep-auditable mandate holds for the third scope as it does
    for the other two — Showrunner never links anything across events, and there is no shared key for
    it to leak through. Written by `directors/story/world.py`.
    """
    return f"{event_id}:world"


def person_scope(event_id: str, person_id: str) -> str:
    """The per-person scope: `{eventId}:{personId}` (spec 11 §4, verbatim).

    Used by `directors/story/taste.py::write_memo_for` — the only writer of a per-person scope
    anywhere in the fleet, which is what keeps this one function the single place that knows how a
    Memory Bank key is spelled.
    """
    return f"{event_id}:{person_id}"


async def recall_host_preferences(event_id: str, event: dict[str, Any] | None = None) -> str:
    """The host's standing preferences as one short paragraph, or `""` if they never typed any."""
    engine = settings().agent_engine_id
    if engine:
        text = await _from_memory_bank(engine, event_id)
        if text:
            return text[:MAX_PREFERENCE_CHARS]
    return _from_firestore(event_id, event)[:MAX_PREFERENCE_CHARS]


def _from_firestore(event_id: str, event: dict[str, Any] | None) -> str:
    doc = event if event is not None else (fs.get_event(event_id) or {})
    raw = doc.get("hostPreferences") or ""
    return str(raw).strip()


async def _from_memory_bank(engine: str, event_id: str) -> str:
    """`VertexAiMemoryBankService.search_memory` at the `{eventId}:host` scope.

    Wrapped in a bare except on purpose: this is soft context. A Memory Bank that is unreachable, not
    provisioned, or returns nothing must degrade to "the host has no standing preferences", never to
    a failed tick — the director's job is coverage, not recollection.
    """
    try:
        from google.adk.memory.vertex_ai_memory_bank_service import VertexAiMemoryBankService

        cfg = settings()
        service = VertexAiMemoryBankService(
            project=cfg.project, location=cfg.location, agent_engine_id=engine
        )
        response = await service.search_memory(
            app_name="showrunner", user_id=scope(event_id), query="host preferences for this event"
        )
        chunks: list[str] = []
        for entry in getattr(response, "memories", None) or []:
            content = getattr(entry, "content", None)
            for part in getattr(content, "parts", None) or []:
                if getattr(part, "text", None):
                    chunks.append(str(part.text).strip())
        return " ".join(chunks).strip()
    except Exception as exc:  # noqa: BLE001 - see docstring
        log.warn("memory_bank_recall_failed", event_id=event_id, err=str(exc))
        return ""


async def remember_world_model(event_id: str, prose: str) -> None:
    """Mirror the distilled venue paragraph into Memory Bank at `{eventId}:world`.

    A mirror, not the system of record: `ledger/worldModel` holds it (`shared/fs.py::world_model_ref`),
    and `directors/story/world.py::recall_prose` reads it from there. This call exists because the
    paragraph is genuinely the kind of thing Memory Bank is for — narrative context about an event —
    and because a future agent reasoning across a session should be able to recall it the same way it
    recalls the host's standing preferences.

    What it must never become is the *source* of that paragraph for anything that decides an outcome.
    The counts behind it live on the coverage shards and are read straight from there by the ranking
    (`publisher/program.py`); this text only ever explains. Same mandate as the module docstring, and
    the same best-effort posture as `remember_taste_memo` below: an unreachable Memory Bank must not
    undo a distillation that already computed and already persisted.
    """
    engine = settings().agent_engine_id
    if not engine or not prose:
        return
    try:
        from google.adk.memory.memory_entry import MemoryEntry
        from google.adk.memory.vertex_ai_memory_bank_service import VertexAiMemoryBankService
        from google.genai import types as genai_types

        cfg = settings()
        service = VertexAiMemoryBankService(
            project=cfg.project, location=cfg.location, agent_engine_id=engine
        )
        entry = MemoryEntry(
            content=genai_types.Content(role="user", parts=[genai_types.Part(text=prose)])
        )
        await service.add_memory(
            app_name="showrunner", user_id=world_scope(event_id), memories=[entry]
        )
    except Exception as exc:  # noqa: BLE001 - see docstring
        log.warn("memory_bank_world_write_failed", event_id=event_id, err=str(exc))


async def remember_taste_memo(event_id: str, person_id: str, memo: str) -> None:
    """Write a taste memo into Memory Bank at `{eventId}:{personId}` (spec 07 §2/§4.1).

    Firestore (`people/{personId}/private/profile.tasteMemo` — the deny-all subcollection, because a
    memo about what a guest likes must not sit on a document every event member can read) is the
    system of record. Spec 07 §3's personalised ranking is the intended reader and is **not built**:
    nothing in the backend reads `tasteMemo` or `tasteProfile` today except the deletion/export path
    in `api/identity.py`. Said plainly because the previous version of this line named
    `directors/reel/select.py` as a live reader, which would send the next person looking for a
    consumer that does not exist — and when one *is* written it must read `fs.person_private_ref`,
    not the person document. This call is soft and
    best-effort for the same reason `_from_memory_bank` degrades silently: an unreachable or
    unprovisioned Memory Bank must never undo a memo cycle that already computed a real analysis and
    already persisted it. `add_memory` (not `add_session_to_memory`) is used because there is no
    conversation to replay into a session — the memo already *is* the distilled memory.
    """
    engine = settings().agent_engine_id
    if not engine or not memo:
        return
    try:
        from google.adk.memory.memory_entry import MemoryEntry
        from google.adk.memory.vertex_ai_memory_bank_service import VertexAiMemoryBankService
        from google.genai import types as genai_types

        cfg = settings()
        service = VertexAiMemoryBankService(
            project=cfg.project, location=cfg.location, agent_engine_id=engine
        )
        entry = MemoryEntry(
            content=genai_types.Content(role="user", parts=[genai_types.Part(text=memo)])
        )
        await service.add_memory(
            app_name="showrunner", user_id=person_scope(event_id, person_id), memories=[entry]
        )
    except Exception as exc:  # noqa: BLE001 - see docstring
        log.warn("memory_bank_taste_write_failed", event_id=event_id, person_id=person_id, err=str(exc))
