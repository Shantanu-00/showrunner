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


def person_scope(event_id: str, person_id: str) -> str:
    """The per-person scope: `{eventId}:{personId}` (spec 11 §4, verbatim).

    Unused by this session — spec 07's taste memos are B4-S13's — and defined here anyway so that
    when they land there is exactly one place that knows how a Memory Bank key is spelled.
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
