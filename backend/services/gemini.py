"""Gemini access for the perception fleet — one ADK agent, run once, parsed or classified.

Two things here are load-bearing and were both learned the expensive way (friction log 2026-08-27):

1. **The publisher models serve from `global`, not `us-central1`.** A `us-central1` call 404s with
   "model not found", which reads like a wrong model ID and is not. The fix is scoped to the model
   wrapper via `client_kwargs` rather than the process-wide `GOOGLE_CLOUD_LOCATION`, because that
   variable is what builds Cloud Tasks queue paths — flipping it to `global` would trade a model
   404 for a dispatch 404.
2. **Failures split into transient and permanent before they reach the caller.** A worker's retry
   decision is the difference between absorbing a rate limit and a retry storm on a poisoned photo
   (spec 03 §6), and the only place that has enough information to make that call is here, where
   the exception still exists.

Guest media is only ever sent on the billed first-party path (`enterprise=True`): the AI Studio
free tier trains on submitted data, which is a line this project states in its README and does not
cross for anyone's wedding photos.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from typing import Any, TypeVar

from google.adk.agents import LlmAgent
from google.adk.models import Gemini
from google.adk.runners import InMemoryRunner
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import BaseModel, ValidationError

from shared import log
from shared.settings import settings

T = TypeVar("T", bound=BaseModel)

#: Statuses worth retrying. Everything else is the same answer on every attempt.
_TRANSIENT_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})

#: A stop for any reason other than these means we did not get a complete answer.
_CLEAN_FINISH = frozenset({"STOP", "MAX_TOKENS", None})


class ModelError(RuntimeError):
    """Base for model-call failures, carrying the classification the caller acts on.

    `usage` is whatever the failed call already burned. A refusal on the second attempt still cost
    two prompts, and a cost ticker that only counts successes is a cost ticker that lies.
    """

    usage: "ModelUsage"

    def __init__(self, message: str, usage: "ModelUsage | None" = None) -> None:
        super().__init__(message)
        self.usage = usage or ModelUsage()


class TransientModelError(ModelError):
    """Rate limits, timeouts, 5xx. Hand back to Cloud Tasks and let the queue absorb it."""


class PermanentModelError(ModelError):
    """Refusal, unparseable output after a retry, bad request. Retrying buys nothing."""


@dataclass(frozen=True)
class ModelUsage:
    """Token accounting, summed onto the media doc for the cost ticker (spec 03 §3).

    `tokensCached` is a subset of `tokensIn`, not an addition to it: Vertex reports the portion of
    the prompt that was served from an implicit context cache, and those tokens are billed at a
    fraction of the full rate. Tracked separately because the rubric, the few-shots and the event
    context block are byte-identical across every photo of an event, so this is the difference
    between the spend calibration in spec 09 §2 holding and not.
    """

    tokensIn: int = 0
    tokensOut: int = 0
    tokensCached: int = 0

    def __add__(self, other: "ModelUsage") -> "ModelUsage":
        return ModelUsage(
            self.tokensIn + other.tokensIn,
            self.tokensOut + other.tokensOut,
            self.tokensCached + other.tokensCached,
        )


@functools.lru_cache(maxsize=8)
def adk_model(model_id: str) -> Gemini:
    """An ADK model wrapper pinned to the billed enterprise path in the GenAI region."""
    cfg = settings()
    return Gemini(
        model=model_id,
        client_kwargs={
            "enterprise": True,
            "project": cfg.project,
            "location": cfg.genai_location,
        },
    )


#: Keyed by agent name, not by the agent object — `LlmAgent` is a pydantic model and unhashable,
#: so `lru_cache` on it raises at the first call.
_RUNNERS: dict[str, InMemoryRunner] = {}


def _runner(agent: LlmAgent) -> InMemoryRunner:
    """One runner per agent for the process lifetime.

    Sessions are created and deleted per call instead: the perception agents are deliberately
    stateless (one photo, one judgment — Sessions and Memory Bank belong to the directors,
    spec 05), and an accumulating in-memory session store in a long-lived worker is a leak.
    """
    runner = _RUNNERS.get(agent.name)
    if runner is None:
        runner = InMemoryRunner(agent=agent, app_name="showrunner")
        _RUNNERS[agent.name] = runner
    return runner


def _classify_error(exc: Exception) -> ModelError:
    status = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if isinstance(exc, genai_errors.ServerError):
        return TransientModelError(f"server error {status}: {exc}")
    if isinstance(exc, genai_errors.ClientError):
        if status in _TRANSIENT_STATUS:
            return TransientModelError(f"throttled {status}: {exc}")
        return PermanentModelError(f"client error {status}: {exc}")
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return TransientModelError(f"{type(exc).__name__}: {exc}")
    # An unrecognised exception is treated as transient exactly once by the queue; the attempt
    # ceiling in the handler stops it from becoming a storm.
    return TransientModelError(f"{type(exc).__name__}: {exc}")


async def _invoke(agent: LlmAgent, parts: list[types.Part]) -> tuple[str, ModelUsage, str | None]:
    """One agent turn. Returns (raw text, usage, finish reason)."""
    runner = _runner(agent)
    session = await runner.session_service.create_session(
        app_name="showrunner", user_id="worker"
    )
    text_chunks: list[str] = []
    usage = ModelUsage()
    finish: str | None = None
    error_message: str | None = None
    try:
        async for event in runner.run_async(
            user_id="worker",
            session_id=session.id,
            new_message=types.Content(role="user", parts=parts),
        ):
            if event.usage_metadata is not None:
                usage = ModelUsage(
                    tokensIn=int(event.usage_metadata.prompt_token_count or 0),
                    tokensOut=int(event.usage_metadata.candidates_token_count or 0),
                    tokensCached=int(event.usage_metadata.cached_content_token_count or 0),
                )
            if event.finish_reason is not None:
                finish = str(getattr(event.finish_reason, "name", event.finish_reason))
            if event.error_message:
                error_message = event.error_message
            if event.is_final_response() and event.content and event.content.parts:
                text_chunks += [p.text for p in event.content.parts if p.text]
    except Exception as exc:  # noqa: BLE001 - classified, then re-raised as ours
        raise _classify_error(exc) from exc
    finally:
        try:
            await runner.session_service.delete_session(
                app_name="showrunner", user_id="worker", session_id=session.id
            )
        except Exception as exc:  # noqa: BLE001 - a leaked session must not fail the request
            log.debug("session_delete_failed", err=str(exc))

    if error_message:
        raise PermanentModelError(f"model reported: {error_message}", usage)
    return "".join(text_chunks), usage, finish


async def run_structured(
    agent: LlmAgent,
    parts: list[types.Part],
    schema: type[T],
    *,
    stage: str,
) -> tuple[T, ModelUsage]:
    """Run `agent` and return its output parsed into `schema`.

    A schema-invalid response gets exactly **one** retry (spec 03 §6) before it is declared
    permanent: the first bad parse is usually a truncation or a stray code fence, and a second
    identical failure is a prompt/schema problem that no number of retries will fix. Usage from
    both attempts is billed to the caller, because both attempts were paid for.
    """
    total = ModelUsage()
    last: Exception | None = None

    for attempt in (1, 2):
        raw, usage, finish = await _invoke(agent, parts)
        total = total + usage

        if finish not in _CLEAN_FINISH:
            # SAFETY, RECITATION, BLOCKLIST… — a refusal, not a malformed answer.
            log.warn("model_refused", stage=stage, finish=finish, attempt=attempt)
            raise PermanentModelError(f"model stopped with finish_reason={finish}", total)

        try:
            return schema.model_validate_json(_strip_fence(raw)), total
        except (ValidationError, ValueError) as exc:
            last = exc
            log.warn(
                "model_output_unparseable",
                stage=stage,
                attempt=attempt,
                err=str(exc)[:200],
                raw=raw[:200],
            )

    raise PermanentModelError(f"schema-invalid after 1 retry: {last}", total)


def _strip_fence(raw: str) -> str:
    """Tolerate a ```json fence. Cheaper than a retry, and costs nothing when absent."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1] if "\n" in text else text
        if text.endswith("```"):
            text = text[: -len("```")]
    return text.strip()


def as_image_part(data: bytes, content_type: str) -> types.Part:
    return types.Part.from_bytes(data=data, mime_type=content_type)


def as_text_part(text: str) -> types.Part:
    return types.Part.from_text(text=text)


def usage_increments(usage: ModelUsage) -> dict[str, Any]:
    """Firestore increments, so two workers finishing at once both get counted."""
    from google.cloud import firestore

    return {
        "usage.tokensIn": firestore.Increment(usage.tokensIn),
        "usage.tokensOut": firestore.Increment(usage.tokensOut),
        "usage.tokensCached": firestore.Increment(usage.tokensCached),
    }
