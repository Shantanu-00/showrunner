"""`ModelArmorPlugin` — Model Armor as an ADK plugin, in front of the model rather than beside it.

Two places can hold a guardrail: at the surface that accepts the text (`armor.guard()`, called by
the host wizard's itinerary paste and the bounty-brief writer), or at the boundary where text
actually reaches a model. Both are worth having, and they fail differently: the surface check is
precise about *what* it rejected and can tell a human, while this one catches everything that reached
a prompt by any route — a tool result, a Memory Bank recollection, a caption threaded into a director
brief three hops from where it was typed. Spec 05's Story Director assembles its prompt from exactly
those kinds of inputs, so the surface check alone would be a list of places to remember.

Design notes:

- **Attached to the directors, not the perception workers.** A per-photo Armor call would add a
  network round trip to a stage the queue rates are calibrated to (spec 09 §2) in order to inspect
  text the *host already had checked at onboarding* — the event name and the reviewed glossary. The
  directors run on a 2-minute tick and assemble untrusted text from many sources, which is where the
  cost/benefit inverts. `services.gemini.run_structured(..., plugins=[...])` is how a caller opts in.
- **Blocking is a `PermanentModelError`, by construction.** The plugin returns an `LlmResponse`
  carrying `error_message`, which is exactly what `services.gemini._invoke` already turns into a
  permanent failure. So a deflected prompt takes the conservative path every worker already has
  instead of needing a second failure mode: no retry storm on text that will match again.
- **Each distinct string is checked once per plugin lifetime.** An agent turn resends its whole
  history, so an unguarded implementation would re-sanitize the same itinerary on every hop of a
  10-tick session. Hashes only are retained — never the text.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections import OrderedDict

from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.plugins.base_plugin import BasePlugin
from google.genai import types

from shared import log

from . import armor

#: Bounded so a long-lived Agent Runtime process cannot grow this without limit.
_MEMO_LIMIT = 512


class ModelArmorPlugin(BasePlugin):
    """Screens user-authored text in every outbound model request (spec 09 §4.5, spec 10)."""

    def __init__(self, *, surface: str = "prompt", event_id: str | None = None) -> None:
        super().__init__(name="model_armor")
        self.surface = surface
        self.event_id = event_id
        self._seen: OrderedDict[str, bool] = OrderedDict()

    # ------------------------------------------------------------------ helpers

    def _memo(self, digest: str) -> bool | None:
        if digest not in self._seen:
            return None
        self._seen.move_to_end(digest)
        return self._seen[digest]

    def _remember(self, digest: str, matched: bool) -> None:
        self._seen[digest] = matched
        self._seen.move_to_end(digest)
        while len(self._seen) > _MEMO_LIMIT:
            self._seen.popitem(last=False)

    @staticmethod
    def _candidate_texts(llm_request: LlmRequest) -> list[str]:
        """Every piece of text that did not come from us.

        `role == 'user'` covers both the guest/host-authored message and function responses handed
        back to the model, which is where a tool's return value — a caption, a scraped itinerary —
        re-enters the prompt. The agent's own instruction is not checked: it is our source code, and
        sending it to a filter on every call would be paying to inspect ourselves.
        """
        texts: list[str] = []
        for content in llm_request.contents or []:
            if getattr(content, "role", None) != "user":
                continue
            for part in content.parts or []:
                if part.text and part.text.strip():
                    texts.append(part.text)
                response = getattr(part, "function_response", None)
                if response is not None and getattr(response, "response", None):
                    texts.append(str(response.response))
        return texts

    # ------------------------------------------------------------------ the callback

    async def before_model_callback(
        self, *, callback_context, llm_request: LlmRequest
    ) -> LlmResponse | None:
        for text in self._candidate_texts(llm_request):
            digest = hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()
            remembered = self._memo(digest)
            if remembered is False:
                continue
            if remembered is True:
                return self._deflect(["previously matched"])

            verdict = await asyncio.to_thread(armor.sanitize, text, surface=self.surface)
            self._remember(digest, verdict.matched)
            if verdict.matched:
                if self.event_id:
                    # Same `ops/` record `armor.guard()` writes, so the Flight Deck's deflected chip
                    # comes from one place regardless of which layer caught it.
                    await asyncio.to_thread(
                        _alert, self.event_id, self.surface, verdict.filters
                    )
                return self._deflect(verdict.filters)
        return None

    def _deflect(self, filters: list[str]) -> LlmResponse:
        """An error-only response — deliberately with no `content`.

        A deflection that carried explanatory text as content would be handed to the agent's
        `output_schema` parser and fail as a *JSON* error, which `services.gemini` then classifies as
        transient and retries — the exact wrong outcome for text that will match on every attempt
        (measured 2026-08-28; the first version of this method did precisely that). With `error_message`
        alone the runner emits an error event, `_invoke` raises `PermanentModelError`, and the caller's
        existing conservative-default path handles it. The block belongs in the error channel, not in
        the answer channel.
        """
        detail = ", ".join(filters) or "policy match"
        log.warn("armor_prompt_deflected", surface=self.surface, event_id=self.event_id, filters=detail)
        return LlmResponse(
            error_code="MODEL_ARMOR_BLOCKED",
            error_message=f"Model Armor blocked this prompt ({detail})",
        )


def _alert(event_id: str, surface: str, filters: list[str]) -> None:
    from shared import fs

    fs.ops_alert(
        event_id,
        "armor_blocked",
        f"Model Armor deflected a prompt on {surface}: {', '.join(filters) or 'policy match'}",
        severity="warning",
        surface=surface,
        filters=filters,
    )
