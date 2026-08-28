"""What the wrap-report writer model is allowed to return (spec 08 §2 step 3).

One field, deliberately. Every number in the wrap report — photo/reel/photographer counts,
per-stage coverage, the honest-gaps list — is computed in `backend/api/host.py` from real Firestore
aggregates and the coverage ledger before this model is ever called, per spec 12's "truthful by
construction: any number on screen traces to a real Firestore aggregate" (never the other way
round: an LLM synthesizing a summary must not be trusted to also invent the figures inside it).
The model's only job is to turn already-computed facts into one readable sentence for the console
and the kiosk finale slide. If it refuses or the call fails, the caller falls back to a plain
deterministic sentence — a wrap report must never block wrapping the event.
"""

from __future__ import annotations

from pydantic import BaseModel


class WrapHeadlineOut(BaseModel):
    headline: str  # one sentence, grounded only in the facts given in the prompt
