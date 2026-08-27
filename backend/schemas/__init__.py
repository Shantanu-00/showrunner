"""Pydantic schemas — the cross-lane contract.

These shapes are what the backend writes to Firestore and what `frontend/src/lib/types.ts`
mirrors in TypeScript. Later sessions add `curator_out`, `verdict`, `bounty`, `edl` and `reel`
(specs 05/06); the perception blocks they refine already live on `media.MediaDoc`.
"""
