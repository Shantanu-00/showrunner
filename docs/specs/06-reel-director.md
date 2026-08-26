# Spec 06 — Reel Director (story manufacture, anti-generic design, versioning)

Goal: reels that feel *edited for this wedding*, not templated; produced autonomously; that improve themselves as better photos arrive; and that never leak a vetoed asset.

## 1. Commissions, not hardcoded agents

One parameterized director, many **commissions** (from Story Director actions, stage-end events, or host button):

```
reels/{reelId}: {
  persona: couple | stage_recap:{stageId} | guest_energy | main_character:{personId},
  audienceRing: 2 (public) | 1 (private-to-person),      # main_character reels are PRIVATE by default
  status: directing → composing → rendering → published | superseded | unpublished,
  narrativeBrief, storyboard, edl, musicBrief, lyriaClipUri,
  candidateSnapshotAt, assetManifest: [mediaId…], version, previousVersionId?, outputUri
}
```

**Scope guard — personal reels are commissioned, never fanned out.** A `main_character` reel exists only on explicit commission: a Story Director action, a host action, or an enrolled guest's own request. It is never auto-generated per guest — 500 guests × ~3 min/render on a concurrency-2 renders queue is ~12.5 hours of wall clock, so per-guest fan-out is mathematically not a same-night product regardless of API budget. The honest product line: every guest leaves with a personal *album* (a free query over existing state); a personal *reel* is an on-demand artifact.

## 2. Why it won't be generic (the design answer, explicitly)

Genericness comes from templates choosing content. Here the content chooses the structure:

1. **Evidence in, narrative out.** The director's input is the *actual* candidate media as structured evidence: captions, momentTags, emotion signals (Vision joy + curator), face composition, timestamps, bounty provenance. Step one is a **narrative brief** — 3–5 sentences about *what actually happened*: "This Haldi belonged to the groom's friends — the turmeric fight escalated for 20 minutes; the emotional anchor is his mother wiping his face at the end." Two events (or two personas at one event) produce different briefs because the evidence differs.
2. **Persona lens.** Same evidence, different editorial mandate: `couple` = intimacy arc (glances, hands, quiet moments); `guest_energy` = kinetic montage (dance, laughter, crowd); `main_character:X` = X's day in order, weighted by X's taste profile (spec 07); `stage_recap` = ritual structure. The lens changes *selection and pacing*, not a color filter.
3. **Structural degrees of freedom the LLM actually exercises:** shot count (10–24), pacing curve (linear build / peak-and-settle / two-act), transition palette (3 of ~15 xfade types, chosen to match energy), caption voice (from the brief), music brief (tempo, instrumentation, emotional arc → Lyria). A style seed = hash(eventId, persona, version) varies tie-breaking so even re-runs differ.
4. **Generator + critic loop (named pattern for judges).** A cheap flash-lite critic scores the storyboard against a rubric: references ≥ 3 specific moments by name? non-flat arc? no near-duplicate consecutive shots? persona mandate honored? Score < threshold → one regeneration with the critique appended. Deterministic EDL linter then enforces *technical* validity only (durations sum to music length, assets exist, faces not cropped by pan vectors — face boxes are inputs).

Templates constrain **validity**; evidence and persona determine **content**. That is the one-line answer to "isn't it hardcoded?"

## 3. Pipeline per commission

```
1. SELECT   query candidates: persona filter + visibility ≥ audienceRing + aesthetic floor,
            cap 40, diversity-sampled (momentTag + face-cluster spread), snapshotAt = now.
            VIP floor (spec 11 §3.3): reserve ≥1 slot per tier-0/1 person with eligible media —
            a guaranteed floor, not a sampling-probability boost, because bad luck can still
            exclude someone from a weighted-random draw and never should for the principals.
2. DIRECT   gemini-3.7-flash → narrativeBrief → storyboard {shots[]: mediaId, inSec/outSec (videos),
            durationBeats, kenBurns {from,to} anchored on face boxes, captionLine?, transition}
            + musicBrief {style, tempo, arc, cultural refs}
3. CRITIC   flash-lite rubric pass (≤1 retry)  → EDL linter (deterministic)
4. SCORE    Lyria 3 Clip ($0.04) from musicBrief → librosa beat_track on returned MP3
            → quantize shot boundaries to beat grid (downbeats for emphasis shots)
5. RENDER   Cloud Run Job (8 vCPU/32 GiB): Python builds ffmpeg filtergraph —
            zoompan (Ken Burns) / trim (video subclips, from originals) / xfade / ASS
            captions / acrossfade audio → 1080×1920 H.264 → curated bucket. Job writes
            progress → reel doc (client shows "rendering 60%").
            CAPTIONS (decided): English + Hinglish in LATIN SCRIPT ONLY ("Haldi vibes",
            "Baraat has arrived!") — ffmpeg drawtext cannot shape Devanagari; libass+Noto
            Devanagari is the documented production path, deliberately out of hackathon scope.
            The director's prompt instructs Latin-script captions explicitly.
6. PUBLISH  recompute reel visibility (audienceRing + all manifest assets still eligible)
            → kiosk premiere slot (public) or album notification (private)
```

Renders are ~2–5 min; commissions are serialized per persona (one active render each) via a `renders` Cloud Tasks queue with concurrency 2.

**Render races:** if a constituent asset is deleted mid-render (guest exercises delete-my-data), the job fails cleanly → the failure handler recommissions a *revision* without that asset (one automatic attempt, then `ops/` alert). The publish step always re-validates the full `assetManifest` against current visibility before going live — a render started eligible can still be refused publication.

## 4. Late-arriving better candidates — the explicit policy

**Never inject mid-render.** A render is an immutable function of `candidateSnapshotAt` + EDL. Instead: **versioned supersession.**

- On relevant triggers (stage end, every 15 min while its stage is active, bounty fulfilled), a cheap evaluator compares new `indexed` media against the published reel's weakest shots (same moment slot, aesthetic delta > 0.15, or a previously-missing required moment now covered).
- Improvement found → commission **v(n+1)**: the director *revises* (previous storyboard + new evidence in context — "swap, don't rewrite, unless the new asset changes the story"). Re-render ≈ $0.05 Lyria reuse + ~$0.10 compute.
- Debounce: ≤ 1 supersession per persona per 30 min; hard stop at **final cut** (stage end + 30 min for recaps; event end for couple/energy reels). Old version marked `superseded` (kept for the host's archive); kiosk playlist swaps atomically on publish.
- Demo line: *"the reel re-edited itself when a better photo of the varmala arrived."* Kiosk behavior for the same question: the kiosk is already fully live (spec 04 §4) — new candidates enter hero rotation in seconds; reels are the only artifact with render latency, hence versioning.

## 5. Collages & hero moments

- Collages: same SELECT + a Pillow layout engine (grid/masonry templates, crops anchored on face boxes); published like media.
- Once per event: Veo 3.1 Fast image-to-video 8 s opener from the top couple portrait, prepended to the `couple` reel v-final. Nano Banana 2 stylized portraits (P1) live in private albums as a "claim your portrait" card.

## 6. Cost & duration budget

30 s reel = ~15 shots: direct+critic ≈ $0.02, Lyria $0.04, render ≈ $0.10 compute → **< $0.20/version**. A full event with 10 reels × avg 2 versions ≈ $4.

## 7. Safety & consent interlock

`assetManifest` links every published reel to its constituents. Any constituent losing Ring-2 eligibility (veto, consent flip, host pull) → reel `unpublished` immediately (kiosk drops it) → auto-commission of a replacement version without that asset. Private (`main_character`) reels may use Ring-1 media of that person only.

## 8. Acceptance criteria

- [ ] Two personas over the same seeded dataset produce visibly different shot lists, briefs, pacing, and music (diff test asserted on storyboard JSON).
- [ ] Critic loop rejects a deliberately-flat storyboard (fixture) and the retry passes.
- [ ] Cuts land within ±80 ms of beat times (assert against librosa grid on the rendered file).
- [ ] Better-candidate fixture arriving post-publish triggers exactly one v2 within the debounce window; kiosk swaps atomically.
- [ ] Vetoing a constituent photo unpublishes the reel ≤ 5 s and a replacement version renders without it.
- [ ] No face crossing frame edge during any Ken Burns move (linter test on EDL + face boxes).
