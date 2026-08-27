"""Pydantic schemas — the cross-lane contract.

These shapes are what the backend writes to Firestore and what `frontend/src/lib/types.ts`
mirrors in TypeScript. Later sessions add `verdict`, `bounty`, `edl` and `reel` (specs 05/06);
the perception blocks they refine already live on `media.MediaDoc`.

Note the split between `curator_out.CuratorOut` (what the model may return) and
`media.CuratorBlock` (what gets stored). Every agent contract in the system follows that shape:
the model's opinion is one input to a deterministic function, never the stored answer itself.
"""
