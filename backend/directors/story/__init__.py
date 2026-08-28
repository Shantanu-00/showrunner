"""The Story Director (spec 05) — the truest agent in the fleet, and the 40% criterion.

Nobody asks it anything. Every tick it reconciles *what the timeline says should be happening* with
*what the photo stream proves is happening*, and acts: bounties, escalations, stage transitions, reel
commissions. `director.run_tick` is the whole entry point; `agent.story_director()` is the ADK graph
that `adk deploy agent_engine` would package.

Module map, which is also the shape of one tick:

    validate.py  settle bounty submissions that landed since the last tick, award points
    act.py       expire what timed out, arm the new stage's required moments, apply the plan
    ledger.py    LEDGER — deterministic aggregation, no LLM anywhere near it
    agent.py     REASON — the one model call, structured output, guardrailed vocabulary
    session.py   the rolling 10-tick window and its deterministic compaction
    memory.py    Memory Bank, scoped `{eventId}:…`, holding taste and nothing that gates anything
"""
