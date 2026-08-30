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

Firestore security rules (server-enforced, mirrors the tiers; identity comes from **custom claims** — `request.auth.token.personId` / `.hosts` / `.members` per spec 02 §1 — never from `get()` joins):
- `visibility=='public'` → any authed event **member** may read: `isMember(eventId) = isHost(eventId) || isAdmin() || eventId in token.members`.
- `visibility=='pool'` → uploaderUid, host-claim uids, and uids whose `token.personId ∈ media.albumOf[]`.
- `visibility=='self'` → uploaderUid only (`blocked` items additionally surface in the host moderation area via an admin query path).
- Host console: host uids read all non-deleted media (host = data controller for their event).
- `people`, `guests`, `bounties` and published `reels` are member-scoped by the same predicate. The published-reel branch in particular now carries a membership term; it had none, which made a finished film the one thing in the tree a wholly unauthenticated caller could read.
- Client writes allowed only on: upload-intent docs (shape-validated), `reactions`, consent field of own media.

**`isMember` takes an eventId, and that is the whole event boundary.** It used to be spelled `isMember()` — literally `signedIn()`, on the reasoning that anonymous sign-in *is* the act of arriving at an event (spec 02 §1) and that nothing granted on that basis was sensitive. The first half was true; the second half was the defect. An eventId is not a secret — it is in a QR code, a URL and a kiosk address bar — so "signed in" meant any anonymous session holding *any* eventId could read that event's public wall, its `people` names and tiers, its `guests` leaderboard, its `bounties` and its published reels. Every event was effectively a public event protected by nothing but a link. Membership is now a claim minted by **`POST /v1/events/{eventId}/join`** (spec 08 §3), which on an invite-only event demands the host's hashed code and takes a seat under a transactional cap. A claim rather than a membership document because no rule here may `get()`; an array rather than a scalar because one phone attends more than one event.

**Membership and the rings are two axes and neither is expressed in the other.** Membership decides *who is at the event*; the consent rings decide *what they may see*. `recompute_visibility` above keeps exactly the inputs listed in its body — nothing about consent changed, and no media document gained an audience field. Every client signs in anonymously and runs `POST /join` on the same page load (spec 12 §5.1), including the kiosk, so an open event's surfaces pay nothing for this; what it costs is that a bare `curl` of a photo document gets nothing, and an eventId scraped off someone else's QR code is worth nothing on its own.

**Private-per-person data lives in a subdocument, because a rule grants whole documents.** `uidLinks`, `tasteProfile`, `tasteMemo`, `tasteMemoAt` and `lastMemoReactionCount` sit in `people/{personId}/private/profile`, which is denied to **every** client including the host, and is read and written only with service credentials. The person document itself has to stay member-readable — the kiosk's credit chip, the leaderboard's names and the tier→`vipWeight` lookup all need it (§4, spec 11 §3.3) — and Firestore cannot grant a document while withholding one field, so "private" has to mean a different document. Two of those fields are why this matters concretely: `uidLinks` maps every anonymous session at the event back to a named human, which is the exact correlation anonymous sign-in exists to prevent; and a Gemma-authored memo about what a guest likes (spec 07 §2) was the most personal prose anywhere in the system, sitting on a document any member could read. Deny-all includes the host on purpose — a console has no use for either, and "the host can see it" is how something like this ends up in a screenshot.

**Two residual exposures, stated rather than papered over.** Both are known shapes, not omissions:
- A member reads whole `media` documents, which carry `albumOf` (the personIds in frame), `subjectVetoes`, the `guardian` verdict and the per-item `usage` cost. Splitting the one document every worker in the fleet writes to was considered and deliberately not done at this point in the build; the fields are event-internal rather than personal (`albumOf` holds opaque personIds, and the display names they resolve to are already on the wall).
- `events/{eventId}/kiosk/{document}` stays `allow read: if true` (spec 09 §3 verbatim). It cannot be tightened here: deciding by access mode would require reading `events/{eventId}.access.mode`, and that is a `get()`. So a private event's playlist does disclose mediaIds, stage ids, a theme and the publisher's stored ranking factors — no names, no uids, no captions, and no bytes. What makes a private event's wall dark to an outsider is that every collection the playlist points *into* is member-gated above, and that `api/media.py` and `api/reels.py` refuse the unauthenticated byte path when the mode is `invite`. A non-member reading the document gets a programme of opaque ULIDs it cannot resolve into a name, a caption or a pixel.

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
- `stageMatch`: active stage ×1.0, previous ×0.4 — the wall follows the event. (Spec 13: "active" resolves `stageOverride || activeStage || the schedule`, one shared resolver on every surface; "previous" is the latest-starting stage before the active one **by time**, with array order only as the undated fallback.)
- `vipWeight`: deterministic multiplier from each pictured person's `tier` field (spec 11 §3.3: tier 0=3.0, 1=1.8, 2=1.3, 3=1.0 — take the max across faces in frame). Metadata, not memory: no LLM decides who's prominent, the host does at onboarding. Because it's the *max across faces*, a guest photographed **with** a Principal inherits the ×3.0 — a guest's best route to the big screen is being in frame with the couple, which is exactly the social dynamic the kiosk should reward; pure guest shots still rotate in via `diversityPenalty` and `just_in`.

**`just_in` is the "your photo is on the wall" guarantee:** the strip shows public items ordered by upload recency only — no score term, no curation. Note the interplay with `publicFloor`: since the floor lives inside `recompute_visibility` (§2), a sub-floor upload never becomes `public` at a real event (the host's declared quality bar for their own wall — honest and intended). For the judge path this would read as breakage, so the `protected_demo` event sets the ordinary `event.publicFloor` to `0.0` (spec 09 §5; the `demoConfig` override was deleted in S14): there, consent + Guardian alone decide `public`, any test shot reaches the strip within seconds, and quality still governs *hero* curation through the aesthetic term. Floor-free never means safety-free — Guardian and consent gates apply in full everywhere.

**"Why this photo?" overlay (off by default, unlocked by `?explain=1` — glass-box ranking):** the publisher stores the factor breakdown it computed for each slot (`{aesthetic, recency, diversity, stageMatch, vipWeight, rank}`) on the slot object; tapping a kiosk slot or gallery Highlight with the overlay unlocked renders those stored numbers plus the gates (`consent ✓ · Guardian ✓`) as a small card. Zero new computation, zero LLM — it displays what was already decided, which is the point: the same truthful-by-construction discipline applied to ranking. Off for an ordinary guest session, because a ranked breakdown of who is on the wall reads as a leaderboard of faces.

The switch is spelled `?explain=1` and it is **an explain-the-ranking switch, not a mode**: it changes no query, no `visibility`, no ordering, and nothing the server does — it only reveals numbers the publisher already stored. The name matters for the same reason (spec 12 §1): the old spelling `?judge=1` implied a parallel behaviour path, and a parallel behaviour path is exactly what this must never be. `?judge=1` is still accepted so links already shared keep working; nothing generates it.

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

**Kiosk client realities:** browsers block unmuted autoplay — the kiosk operator taps once at setup ("Start show"), which unlocks audio for reel premieres and acquires a **Screen Wake Lock**. The client is resilient to Wi-Fi drops (Firestore listeners reconnect; last playlist cached and looped offline). It signs in anonymously like every other client, and on an invite-only event it becomes a **member** through a kiosk link (`POST /v1/events/{eventId}/kiosk-links`, spec 08 §3) — which grants `members` and deliberately never `hosts`, because a venue TV in a public room is the last device that should be able to wrap the event.

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
- [ ] "Why this photo?" overlay shows only publisher-stored factors (code review: no recomputation path); it is absent from a plain guest session and appears only under `?explain=1`, and unlocking it provably changes no query, no `visibility` and no ordering (same grid, same order, extra card).
- [ ] Cross-event denial in both directions: a uid holding `members: [A]` reads none of event B's `media`, `people`, `guests`, `bounties` or published `reels`, and a uid holding `members: [B]` reads none of A's — verified as explicit rows in the rules matrix, not inferred from A's passing rows.
- [ ] A signed-in uid that has **not** run `POST /join` reads nothing at all from an event beyond its own uploads, even holding a valid eventId; running `POST /join` on an open event grants the public tier on the same page load.
- [ ] `people/{personId}/private/profile` is unreadable by a guest, a subject, *and* the host (rules matrix asserts all three), while `people/{personId}` itself stays member-readable so the leaderboard and Highlights ordering still work.
