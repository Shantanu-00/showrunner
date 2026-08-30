"""REASON — the Story Director's one model call (spec 05 §1, step 2).

Everything around this file is deterministic: `ledger.py` decides what is true, `act.py` decides what
is permitted, `validate.py` decides who gets paid. This is the only place in the control plane where a
language model is asked for a judgment, and the judgment is narrow: *given a reconciled picture of the
event, what is worth doing about it right now.*

**On why this is a plain `LlmAgent` and not an ADK `SequentialAgent` of three sub-agents.** Spec 05 §1
describes the tick as "a `SequentialAgent`: Ledger → Reason → Act", and it would be one line to build
it that way — two `BaseAgent` subclasses wrapping the deterministic steps, a three-node trace, a
prettier diagram. It would also be agent-washing by this repo's own test (HANDOFF §5: "a component
earns 'agent' only if it makes context-dependent judgments or plans/chooses actions; otherwise it must
be deterministic code"), and it would trade the proven `services.gemini.run_structured` path — the
retry-once-on-invalid-JSON rule, the transient/permanent classification, the token accounting, the
Model Armor plugin seam — for a hand-rolled runner loop. So the *sequence* is deterministic Python in
`director.py` and the *agent* is the step that actually reasons. `root_agent` below is still the single
importable artifact `adk deploy agent_engine` packages, so nothing about this choice closes that door.

Three prompt decisions carry the quality:

- **The model may only copy identifiers, never invent them.** The ledger block prints every legal
  `targetStage`, `targetMoment`, `targetVip` and `bountyId`. `act.py` re-derives all four from
  Firestore and rejects anything else with a logged reason, so the prompt's job is to make the right
  answer the easy one rather than to be the enforcement.
- **The guardrails are in the prompt as well as in the code.** Not because the prompt enforces them —
  it cannot — but because a model told "you may issue at most two" spends its two on the two worst
  gaps, while a model that proposes six and has four silently dropped has effectively had its
  priorities chosen by list order.
- **NO_OP is named as a good answer.** Spec 05 §5's last acceptance criterion is two consecutive quiet
  ticks producing `NO_OP` with reasoning. The failure mode of a goal-seeking agent on a 30-second
  cadence is bounty spam, and the cheapest fix is to say plainly that doing nothing is usually right.
"""

from __future__ import annotations

import functools

from google.adk.agents import LlmAgent
from google.genai import types

from schemas.director import DirectorPlan
from services.gemini import adk_model, as_text_part
from shared.settings import (
    BOUNTY_DEFAULT_TTL_MINUTES,
    BOUNTY_POINTS_MAX,
    BOUNTY_POINTS_MIN,
    DIRECTOR_MAX_ACTIVE_BOUNTIES,
    DIRECTOR_MAX_NEW_BOUNTIES_PER_TICK,
    STAGE_ADVANCE_MIN_CONFIDENCE,
    settings,
)

from .ledger import Ledger

INSTRUCTION = f"""\
You are the Story Director of a live event: a photo-coverage director, not an assistant. Nobody is
talking to you. Every couple of minutes you are handed a reconciled picture of the event and you decide
what the crowd should photograph next. Return one JSON object per the schema.

Your goal is complete coverage of what matters at this event: every required moment of every stage
that has started, and at least one good photograph of every person the host named, in the stage that is
happening now. You pursue it by asking guests for specific shots.

You may only use identifiers that appear in the input. A stage id, moment id, personId or bountyId you
did not read there will be rejected before it reaches anything, and the tick will have wasted its turn.

Actions, and when each is right:

ISSUE_BOUNTY — a real gap you can still close. Set targetStage and targetMoment, or targetStage and
targetVip, or all three. title is the wanted-poster headline on a five-metre screen: under 60
characters, concrete, no exclamation marks. guestFacingCopy is the sentence in a guest's pocket: say
what to photograph and where, warmly, under 140 characters, never guilt-trip. If a PHYSICAL SETTING
block is present, use it for the "where" — a guest who is told the lawn or the pavilion can walk
there, one told only what to shoot has to guess. Never name a place that block does not name. basePoints {BOUNTY_POINTS_MIN}-{BOUNTY_POINTS_MAX}
(it is multiplied by the person's vipWeight and clamped, so do not pre-multiply). expiresInMin around
{BOUNTY_DEFAULT_TTL_MINUTES} for something happening now, longer for a stage that runs a while.
audience: nearStage for anything a guest must be in the room for, all for a whole-event ask,
assignee for a personal ask (a "group" gap — get everyone in one frame — works best as one; the
system picks WHO deterministically, never you, and re-broadcasts if they don't respond).
Hard limits: at most {DIRECTOR_MAX_NEW_BOUNTIES_PER_TICK} new bounties per tick and {DIRECTOR_MAX_ACTIVE_BOUNTIES} open at once. Spend them on the highest
gaps in the ranked list; a gap for a tier-0 person outranks a tier-3 gap of equal severity.

ESCALATE_BOUNTY — an open bounty marked PAST-HALF-LIFE with no submissions and a gap that still
matters. It gains points, reaches everybody and takes over the big screen. One at a time; a room that
is constantly being shouted at stops listening.

PROPOSE_STAGE_ADVANCE — the drift signal says the photos look like a different stage than the one
marked active. Give toStageId, confidence and the evidence in one clause. It applies automatically at
confidence {STAGE_ADVANCE_MIN_CONFIDENCE} or above when *either* the timetable agrees with the move, or the drift signal has named
that same stage for consecutive ticks (the STAGE DRIFT line tells you its streak) — the photos are the
ground truth the schedule only anticipates. Otherwise it becomes a suggestion for the host, which is a
good outcome, not a failure. Never propose one on the strength of a couple of photos, and never when
the host is holding the stage manually.

COMMISSION_REEL — a stage has ended with good coverage and deserves a film. persona is one of couple,
stage_recap, guest_energy, main_character. At most one per tick.

ANNOUNCE — one short line for the wall when something is starting that guests should walk towards.
Not for congratulating anyone, not for restating a bounty.

NO_OP — the right answer most of the time. Use it when coverage is good, when the gaps are in stages
that have not started, when the bounty budget is spent, or when you already asked for this on a recent
tick and it is too soon to ask again. Give the reason.

Write the assessment first, in two sentences at most: what is happening at this event and what you are
doing about it. It is the only thing you will remember next tick, and the host reads it in the wrap
report, so make it a fact about the event rather than a description of your own actions.
"""


@functools.lru_cache(maxsize=1)
def reason_agent() -> LlmAgent:
    """One agent per process; the event travels in the message, not in the agent (as the Curator does).

    `gemini-3.7-flash` — spec 05 §1's model, and the step in the system where a step up from flash-lite
    is worth paying for: this is planning over a structured world state, not classification.
    """
    return LlmAgent(
        name="story_director",
        description="Reconciles an event's timeline against its photo coverage and decides what to ask for.",
        model=adk_model(settings().model_director),
        instruction=INSTRUCTION,
        output_schema=DirectorPlan,
        output_key="plan",
        generate_content_config=types.GenerateContentConfig(
            # Not zero, unlike the perception agents. Those must score the same photo identically on a
            # replay because an absolute threshold (`publicFloor`) depends on it. This one writes guest-
            # facing copy on a 30-second cadence, and a deterministic director asks for the same thing
            # in the same words every time — which reads, correctly, as a template. Low enough that the
            # *choice* of gap stays stable across a retry; high enough that the sentence does not.
            temperature=0.4,
            # 1024 was not enough and the failure was not obvious: `DirectorAction` is one flat shape
            # covering six action types (schemas/director.py explains why), so the model fills every
            # nullable field of every action it emits — measured at ~180 output tokens per action
            # against ~35 for the fields that action actually uses. A plan with the maximum useful
            # number of actions truncated mid-string, and a truncated JSON object surfaces as a
            # *validation* error, which reads like a prompt problem rather than a budget one.
            max_output_tokens=4096,
        ),
    )


def prompt_parts(led: Ledger) -> list[types.Part]:
    """One text part: the ledger. Everything the director knows, and nothing it does not."""
    return [as_text_part(led.as_prompt_block())]


#: The name `adk deploy agent_engine` looks for. Kept as an alias rather than a second construction so
#: the deployed artifact and the in-process one cannot drift.
def root_agent() -> LlmAgent:
    return reason_agent()
