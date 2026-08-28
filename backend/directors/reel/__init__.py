"""The Reel Director (spec 06) — one parameterized director, many commissions.

Six steps, and which of them are deterministic is the whole design (spec 06 §2's answer to "isn't it
hardcoded?"):

    SELECT   select.py       deterministic  candidates, diversity-sampled, VIP floor
    DIRECT   agent.py        gemini-3.7-flash  evidence → narrative brief → storyboard + music brief
    CRITIC   critic.py       flash-lite rubric (≤1 retry) → deterministic EDL linter
    SCORE    music.py        Lyria 3 clip → librosa beat grid
    EDL      edl.py          deterministic  beat-snapped timings, face-anchored Ken Burns rects
    RENDER   render.py       deterministic  ffmpeg filtergraph → curated bucket
    PUBLISH  store.py        deterministic  re-validate the manifest, then set visibility

`pipeline.py` is the sequence; `commission.py` is how one gets started. Everything runs in one
Cloud Run Job execution per commission (`backend/render/main.py`) — see HANDOFF §4 for why the
director half is not a separate service.
"""
