# Spec 04 — Galleries & Kiosk (how public and private are actually maintained)

Goal: one deterministic function decides what is visible where; galleries are *queries over that function's output*, updated live; the kiosk is a directed show, not a dumb slideshow.

## 1. The core principle

**Judgment by agents, enforcement by policy.** Agents (Curator, Guardian, directors) write *opinions* (scores, verdicts, features). A single deterministic, transactional function — `recompute_visibility` — turns opinions + consent into the `visibility` field. Firestore security rules then serve only what `visibility` allows. No LLM ever directly controls exposure; no race can leak a photo.

## 2. `recompute_visibility(mediaRef)` — the only writer of `visibility`

```python
def recompute_visibility(m) -> str:
    if m.guardian.verdict == 'blocked':                      return 'self'   # forced, consent irrelevant
    if m.deleted or m.duplicateOf or m.consent.ring == 0:    return 'self'   # dupes defer to canonical
    if (m.consent.ring == 2
        and m.guardian.verdict == 'public_ok'
        and m.curator.aestheticScore >= event.publicFloor    # default 0.45
        and not m.subjectVetoes):                            return 'public'
    return 'pool'
```

Called in-transaction by: every perception worker on completion, consent changes, subject vetoes, host review decisions, deletions. Inputs missing (stages pending) → conservative default `pool` visibility. **Every public-surface query additionally filters `status=='indexed'`** — visibility alone is necessary but not sufficient (a half-processed item never leaks onto the kiosk).

Firestore security rules (server-enforced, mirrors the tiers; identity comes from **custom claims** — `request.auth.token.personId` / `.host` per spec 02 §1 — never from `get()` joins):
- `visibility=='public'` → any authed event member may read.
- `visibility=='pool'` → uploaderUid, host-claim uids, and uids whose `token.personId ∈ media.albumOf[]`.
- `visibility=='self'` → uploaderUid only (`blocked` items additionally surface in the host moderation area via an admin query path).
- Host console: host uids read all non-deleted media (host = data controller for their event).
- Client writes allowed only on: upload-intent docs (shape-validated), `reactions`, consent field of own media.

## 3. The galleries (all are live queries, zero polling)

| Surface | Query | Ordering |
|---|---|---|
| **Public gallery** (`/gallery`) | `visibility=='public' && status=='indexed'` (+ stage filter tabs) | `capturedAt` desc; "Highlights" tab: `isHighlight==true` ordered by `aestheticScore × vipWeight(personsInFrame)` (spec 11 §3.3/§3.4) — a *shared* ordering, identical for every viewer; never personalized per visitor |
| **Private album** (`/me`) | `faces array-contains personId` ∧ visibility ∈ {public, pool} — implemented as a maintained membership array `albumOf: [personId…]` on media docs (Firestore can't OR-query; Face Indexer maintains it) | Taste-ranked (spec 07), else `capturedAt` |
| **My uploads** | `uploaderUid == uid` (all rings — it's their own) | `createdAt` desc, with padlock chips; a toast fires the moment one of the uploader's own items flips to `visibility=='public'` (listener on their own docs, spec 11 §3.4) |
| **Host console** | all media + `host_review` queue + ops alerts + coverage ledger | Multiple views |

Listeners bounded: `limit(60)` + cursor pagination; thumbnails (`thumb_384.webp`) in grids, `display_1600` in lightbox. New arrivals animate in via snapshot deltas — this is the "phone → wall in 2 seconds" demo moment.

**Deliberately not built: per-viewer personalization of `/gallery` or the kiosk.** Both are shared feeds by nature — one TV, one public grid, many simultaneous viewers — so "make it about me" belongs in `/me` (already personalized) and the Highlights `vipWeight` factor above (a shared re-ranking, not per-viewer), never in a bespoke per-visitor version of a shared surface. Building the latter would be the kind of scope creep that adds real state-management complexity for no judge-visible payoff.

## 4. Kiosk — a directed show (the "story" of the public screen)

The kiosk is **not** the public gallery on a TV. It's a **playlist** maintained by the publisher (deterministic code, advised by directors), rendered by a dumb fullscreen client.

```
kiosk/playlist: {
  revision, activeStageId, theme,           # per-stage palette/typography (Haldi gold, Pheras crimson)
  slots: [                                   # ~5 min program, recomputed on triggers
    {type: 'hero',        mediaId, holdSec: 6},     # ~60% — fresh highlights of the active stage
    {type: 'reel',        reelId,  premiere: true}, # reel premieres take over the screen
    {type: 'collage',     collageId},
    {type: 'leaderboard', topN: 5},                 # every ~90s
    {type: 'bounty_call', bountyId},                # Story Director escalations: full-screen mission
                                                    # (escalated AND fresh — see the freshness note below)
    {type: 'just_in',     liveWindowSec: 120},      # rolling "just uploaded" strip
  ]
}
```

**Hero selection score** (recomputed on triggers, not per-frame):
`score = aesthetic × recencyDecay(capturedAt, half-life 20 min) × diversityPenalty × stageMatch × vipWeight`
- `diversityPenalty`: don't show the same face-cluster or momentTag twice within 5 slots (prevents "12 consecutive groom photos").
- `stageMatch`: active stage ×1.0, previous ×0.4 — the wall follows the event.
- `vipWeight`: deterministic multiplier from each pictured person's `tier` field (spec 11 §3.3: tier 0=3.0, 1=1.8, 2=1.3, 3=1.0 — take the max across faces in frame). Metadata, not memory: no LLM decides who's prominent, the host does at onboarding. Because it's the *max across faces*, a guest photographed **with** a Principal inherits the ×3.0 — a guest's best route to the big screen is being in frame with the couple, which is exactly the social dynamic the kiosk should reward; pure guest shots still rotate in via `diversityPenalty` and `just_in`.

**`just_in` is the "your photo is on the wall" guarantee:** the strip shows public items ordered by upload recency only — no score term, no curation. Note the interplay with `publicFloor`: since the floor lives inside `recompute_visibility` (§2), a sub-floor upload never becomes `public` at a real event (the host's declared quality bar for their own wall — honest and intended). For the judge path this would read as breakage, so the `protected_demo` event sets the ordinary `event.publicFloor` to `0.0` (spec 09 §5; the `demoConfig` override was deleted in S14): there, consent + Guardian alone decide `public`, any test shot reaches the strip within seconds, and quality still governs *hero* curation through the aesthetic term. Floor-free never means safety-free — Guardian and consent gates apply in full everywhere.

**"Why this photo?" overlay (host/judge mode only — glass-box ranking):** the publisher stores the factor breakdown it computed for each slot (`{aesthetic, recency, diversity, stageMatch, vipWeight, rank}`) on the slot object; tapping a kiosk slot or gallery Highlight in host/judge mode renders those stored numbers plus the gates (`consent ✓ · Guardian ✓`) as a small card. Zero new computation, zero LLM — it displays what was already decided, which is the point: the same truthful-by-construction discipline as the Flight Deck (spec 10), applied to ranking. Never shown to regular guests (it would read as a leaderboard of faces).

**A takeover expires even when the bounty does not (added S14).** An escalated bounty wins the lead
slot, but only while its escalation is *fresh* — `KIOSK_TAKEOVER_FRESH_MINUTES` (12), enforced by the
pure `publisher/program.py::pick_takeover`. Every clause of this spec and of spec 05 §3 was
individually correct and the emergent product behaviour was not: a bounty reaches half-life
unfulfilled, the director escalates it, it takes the whole screen, it expires, the coverage gap is
still open so a fresh one is issued, and the cycle repeats. At a real event a submission breaks it.
On a quiet event nothing does — measured on `dev_demo`, 12 of 16 bounties ended with
`kioskTakeover: true`, i.e. a five-metre screen showing a wanted poster most of the time. Past the
freshness window the bounty is still live and still a banner in every guest's pocket; it just stops
owning the wall. The poster becomes punctuation rather than nagging.

**Recompute triggers (push, not poll):** new `public` highlight; reel published (→ premiere slot inserted next); bounty escalated; stage change (theme + slot flush); every 5 min as fallback. Publisher runs as a small always-warm Cloud Run service with a Firestore listener — **`min-instances=1`** (scale-to-zero would silently kill the listener) **and `--no-cpu-throttling`**, because Cloud Run allocates CPU only during request processing by default and the listener lives on a background thread: an instance that exists without CPU between requests is the same outcome as no instance, with no error to find. A recompute is additionally reachable over HTTP (`POST /recompute`, private, called by the director tick), which is what keeps the wall fresh on a scaled-to-zero deployment where the listener is not running at all.

Writes bump `revision`; the kiosk client transitions on revision change with crossfades — and because that client restarts its program from the first slot on every revision it sees, **the publisher writes only when the program's decisions actually change.** It hashes the slot list, active stage and theme (excluding the stored factor floats, which drift continuously with recency decay) and skips the write when that fingerprint is unchanged. An unconditional rewrite on the 5-minute fallback would reset a real event's wall to slot 0 forever, for nothing.

**Single writer per event, not a global singleton.** A global `max-instances=1` would serialize every concurrent live event's playlist recompute through one process — a correctness bottleneck disguised as a scale limit, and one that directly contradicts spec 08's multi-event design (the Scheduler tick already queries across every event with `status in ('live','wrapping')`). Instead: `max-instances=N` (N=5 default, spec 09 §1), and each instance acquires a transactional lease `publisherLease/{eventId}` (TTL 2 min — same pattern as spec 05 §1's director tick lease) before recomputing that event's playlist. This preserves the *actual* invariant — no two writers ever touch one event's `kiosk/playlist` concurrently — without an artificial global ceiling. Say this in the architecture review: **per-event leader election, not global serialization** (scoring map: `docs/architecture.md` §4.5).

**Kiosk client realities:** browsers block unmuted autoplay — the kiosk operator taps once at setup ("Start show"), which unlocks audio for reel premieres and acquires a **Screen Wake Lock**. The client is resilient to Wi-Fi drops (Firestore listeners reconnect; last playlist cached and looped offline).

**Removal path:** consent flip / veto / host action → `visibility` drops → publisher trigger removes the slot within seconds (acceptance-tested).

## 5. Public reels & collages

Published reels/collages are media-like docs with the same visibility machinery: they enter kiosk + `/gallery` "Reels" tab only when `visibility=='public'`, which requires **every constituent asset** still Ring-2-eligible (the Reel Director records `assetManifest[]`; a veto on any constituent triggers unpublish + re-render — spec 06 §7).

## 6. Acceptance criteria

- [ ] The same photo appears/disappears across all four surfaces purely by flipping consent/veto/review — no code paths bypass `recompute_visibility` (grep-level check: exactly one writer of `visibility`).
- [ ] Security-rules test suite: a stranger uid cannot read `pool` media they don't appear in; a subject can; uploader always can.
- [ ] Kiosk shows no face-cluster twice in any 5 consecutive hero slots (seeded test).
- [ ] Stage change re-themes kiosk ≤ 5 s; reel publish interrupts with a premiere ≤ 5 s.
- [ ] 500 simulated listeners + 5 uploads/s: kiosk p95 update latency < 3 s (fan-out test with the seeded dataset).
- [ ] Two events live simultaneously, each with active kiosk traffic: each event's playlist is owned by exactly one publisher instance at a time (lease-verified); killing one instance mid-lease does not affect the other event's kiosk, and the orphaned lease is reclaimed within its TTL.
- [ ] A deliberately low-aesthetic photo uploaded with public consent to the `protected_demo` event (`event.publicFloor` 0.0) appears in the `just_in` strip < 5 s; the identical upload to a default event (publicFloor 0.45) stays `pool` and never reaches any public surface.
- [ ] "Why this photo?" overlay shows only publisher-stored factors (code review: no recomputation path), and is unreachable from a regular guest session.
