# Spec 02 — Identity, Consent & Privacy

Goal: zero-friction entry (scan QR → uploading in 10 seconds, no account), yet every photo has an owner, consent is never ambiguous, a guest can recover their private album from any device, and privacy defaults protect people who never touch a settings screen.

## 1. Identity model — three layers, loosely coupled

| Layer | What it is | Lifetime |
|---|---|---|
| **`uid`** (Firebase Anonymous Auth) | A browser session identity, created silently on first open. Every upload/reaction is stamped with it. | Per browser profile; lost if storage cleared |
| **`personId`** (`events/{eventId}/people/{personId}`) | A *human* at the event: display name, optional selfie embedding, taste profile, VIP flag. Created at selfie enrollment, name entry, or by the host (VIPs). | Per event |
| **`guestLink`** (`uidLinks` array on person doc) | The join table: which uids belong to which person. A person can have many uids (phone + laptop + re-scan). | Per event |

Guests are **never "verified"** in the account sense — and don't need to be. Uploading requires only a uid. Identity (personId) is only needed to *receive* things: private album, bounty points leaderboard name, personalized reel.

**Custom claims make identity enforceable in security rules.** The server sets Firebase custom claims via the Admin SDK and the client force-refreshes its ID token; Firestore rules read them directly — no `get()` joins per read (rules allow only 10 and they're billed). Three claims carry identity:

| Claim | Means | Granted by |
|---|---|---|
| `personId` | this uid **is** this person | magic-link redemption, or the host approving a held claim (§3) — never a face match on its own |
| `hosts: [eventId…]` | this uid controls these events | event creation, host-link redemption, recovery code (spec 08 §1) |
| `members: [eventId…]` | this uid has arrived at these events | `POST /v1/events/{eventId}/join` (spec 04 §2) |

**`hosts` and `members` are arrays, not scalars,** because one host runs more than one event over a season and one phone attends more than one event — a scalar silently revoked the first of each the moment the second was granted. Firebase caps the whole claims object at 1000 bytes, which puts a ceiling near **32 events per array**; the server refuses the 33rd rather than truncating, because truncating would revoke an event at random. The legacy scalar `host` claim is still honoured, so a console left open mid-event survives the change (an ID token lasts an hour). This is the mechanism behind every rule in spec 04 §2.

**Host identity (decided):** creating an event is unauthenticated and anonymous — **there is deliberately no signup wall in front of "make me an event"**, and there is nothing to authenticate against yet anyway — and returns a **host magic link** + printable recovery code, the same claim-link machinery as guests, with redemption adding the event to the `hosts` array claim. Multiple co-hosts = multiple redemptions; links revocable from the console. Google Sign-In exists, but as an *optional* one-tap upgrade offered on the wizard's success screen beside the recovery code: `linkWithPopup` attaches a Google credential to the **existing** anonymous uid, so every claim minted at creation stays exactly where it is — no migration, no re-grant, nothing server-side to change. Declining costs the host nothing, which is why it is a card and not a step. Full host console + lifecycle: spec 08.

## 2. "They upload without verifying themselves — is it public?"

**No. Nothing is ever public by an identity accident.** Visibility is decided by the consent ring system (§4) + Guardian + quality gates (spec 04), all of which are properties of the *media*, not of whether the uploader enrolled. An anonymous, never-enrolled guest's photos:
- always visible to themselves (their uid),
- enter the **event pool** (host archive + face-matched private albums) by default,
- reach the **public gallery/kiosk only if** they flipped the batch's public toggle AND Guardian passed AND quality floor met.

## 3. Sessions, rescans, and recovery

**Rescan in the same browser:** Firebase anonymous auth persists in local storage → same uid → same albums. Nothing to do.

**Rescan on a new device / cleared storage:** new uid, empty state. Three recovery paths, in UX order:

1. **Magic link (primary).** After first upload or enrollment, the app shows "🔗 Save your album link" (share-to-WhatsApp-self / copy). Server flow:
   - `POST /v1/events/{eventId}/claim-links` (auth: current uid) → generates 128-bit code, stores **hash** in `claimLinks/{hash}: {personId | uid, expiresAt: +30d, revoked: false}` → returns `https://app/…/claim#<code>` (fragment, so the code never hits server logs via referrer).
   - Opening the link: `POST /v1/claim {code}` → verifies hash → mints a **Firebase custom token** for a uid linked to that person → client `signInWithCustomToken` → adds new uid to `uidLinks`. Multi-use within expiry (family shares devices); revocable from settings.
2. **Selfie re-claim.** If the person enrolled a selfie: new session takes a selfie → embedding → `findNearest` against `people` (this event only) → match ≥ **strict** threshold τ_claim (higher than the photo-matching threshold) **with an ambiguity margin**: if the top-2 matches are within 0.08 of each other (twins and siblings are common at weddings), the re-claim is **refused outright (403)** and the UI falls back to magic link / host assist — a review card cannot name *which* twin it means, so there is nothing coherent for a host to approve. A clean match does not hand the album over either: it writes a **held claim** the host approves in the console (below). Anti-abuse: selfies must come from a **live camera capture** (`getUserMedia` / `capture` attribute — no gallery picker), on re-claim a notice event is written to that person's album ("new device joined — not you? tap here"), the caller must have recorded biometric consent (§4) and must not be `banned` (spec 08 §5), and the endpoint is hourly rate-limited per uid. (Honest README note: no liveness detection in the hackathon build; production would add it.)

**No face match ever grants anything on its own.** Every enrollment and every re-claim is written `held` and answers `HELD_FOR_REVIEW` / `PENDING_HOST_APPROVAL`: **the host approves the album before it exists.** The earlier design is worth recording, because its failure mode generalizes. Enrollment used to branch on whether the matched person looked *worth protecting* — `tier ≤ 2` or host-enrolled → review queue; an ordinary self-enrolled tier-3 guest → silently granted that person's identity. That is a guess about which albums are worth stealing, and it was wrong in the only direction that matters: a stranger who saved a photo off the public kiosk could enroll with it and receive that guest's private album, the `personId` claim, and a permanent `uidLinks` entry. Protecting only the people the host happened to name protects nobody else, and "nobody else" is most of the room. So the branch is gone: matching an existing person, matching a VIP reference face, matching an unclaimed cluster, and matching nothing at all all end in the same place — a card in the host's queue. Matching an *unclaimed* face cluster is still the normal, desired path (the claim operates at the **face level** — `findNearest` over face embeddings, claiming all matches ≥ τ_claim regardless of `clusterId`, per spec 03 §5.2 — which subsumes the cluster and is immune to transient duplicate clusters); it simply no longer applies itself.

   **What a held claim holds — and the one flag that makes it hold.** Nothing is granted: no `personId` custom claim, no `uidLinks` entry, no `personId` on any face doc. The enrollee still sees their own uploads throughout, because those were always theirs by `uploaderUid` and never depended on identity (§2). The person document *is* created — the host must have something to look at, and the selfie must live somewhere — carrying **`claimApproved: false`**, and that field is the actual gate:
   - the `personId` claim is minted **only** in the approve branch. It used to be written unconditionally *before* the threshold branch ran, so even a correctly-held claim handed the claim out — the hold was decorative.
   - `worker-face` auto-links a newly detected face only to a person whose `claimApproved` is `true`. This is the non-obvious half, and the reason a flag exists rather than just a claim status: the face indexer matches at `tau_match` (0.45), **looser** than `tau_claim` (0.60), and with no protection check at all. Without the gate a pending enrollment would quietly accrete an album *while sitting in the queue*, and the host would be approving something that had already happened.
   - **Host-enrolled and seeded people are `claimApproved: true` by construction.** The host declaring who someone is *is* the approval; the VIP step (spec 08 §3) is where they do it.

   **Claim-integrity policy layers (bounding the photo-of-a-photo vector):** someone live-captures a printed or displayed photo of a *never-enrolled* guest and claims their unclaimed faces. No liveness detection exists in the hackathon build (disclosed above), so cheap policy carries the weight instead:
   1. **Claim-size gate — now the hold *reason*, not the hold *decision*.** `CLAIM_REVIEW_THRESHOLD` (default **8**, env-configurable) survives, deliberately demoted: since every claim is held, it no longer decides *whether* to hold, only how the card reads — `CLAIM_SIZE` at or above it (this claim would hand over an album), `HOST_APPROVAL` below it, `AMBIGUOUS_MATCH` when two candidate people sit inside the 0.08 margin or a VIP reference face is involved. The card is the same five-second visual check either way (the enrollment selfie beside 4 cluster exemplars), and the enrollee sees "the host is confirming it's you — your own uploads are already in your album." Approve → the `personId` claim is minted, the uid joins `uidLinks`, `claimApproved` flips true, and every face link applies retroactively in one batch. The threshold is still worth keeping precisely because it is only a label: a claim over 8 faces is a claim on someone socially central, and the card should say so out loud.
   2. **Deny reverses, it does not merely stamp.** A denial revokes the `personId` claim (only where it still points at *this* person), and — **when this claim is what created the person** — deletes the person document, the `enrollments/{personId}` selfie template, the `private/` subdocument, and the stored review selfie. Denying a *re-claim* leaves the target person completely untouched: that person existed before the attempt, and the attempt is the only thing being undone. A deny also refuses to delete a person some later claim has since approved. This replaces the earlier rule that a denied enrollment "stands as a new person with zero claimed faces", which belonged to the world where the *enrollment* was trusted and only the *face links* were held. Now that the host approves the album, a deny means "this is not who they say they are" — and leaving a person document plus a stored face template in place would park an unapproved biometric in the match index for the next photo to match against, which is the residue of a refused attempt waiting for a second chance. What does *not* change: no face doc is ever written either way, so a denial leaves the cluster exactly as unclaimed as it found it.
   3. **Claim audit, a readable queue, and a reversible approval.** Every claim, any size, writes `claimAudits/{claimId}: {personId, uid, faceCount, topSimilarity, method: enroll|reclaim|magic_link, status, holdReason, createdPerson, at}` and surfaces in the host console activity feed. The console reads the queue itself through `GET /v1/events/{eventId}/claims?status=`, with `GET …/claims/{claimId}/selfie` (host-gated 302 to a signed URL) supplying the review card's left-hand image. Those two reads are not polish: holding claims with no way to *list* held claims left a correctly-held claim permanently unresolvable, which is how a privacy control turns into a bug report. A wrong approval is recoverable too — `POST …/claims/{claimId}/reverse` undoes a grant (unlink → faces return to unclaimed) — so every direction of this decision is reversible and none of it is silent.
   4. **Inner-circle invite links.** The creation wizard (spec 08 §3, VIP step) offers **"family links"**: named, personId-bound magic links (the §3.1 machinery, nothing new) the host sends over WhatsApp to tier 0–2 people and anyone else they name. Those people skip the selfie path entirely — and since every claim now waits on the host anyway, the inner circle is off the attackable surface twice over.
   5. **Rate limit + ban check on both doors.** `enroll` and `reclaim` are hourly rate-limited per uid and refuse a uid with `guests/{uid}.banned` set. Neither had either check; a claim attempt is a biometric query against a face index, and an unmetered one is a search engine.

   **Consequence worth stating rather than discovering on camera:** because host-approval is now universal and not size-gated, a *first-time* visitor who enrolls on any event — including the `protected_demo` event a walkthrough lands on (spec 09 §4) — sees the pending state, not a populated album, until the host approves. That state is designed, not an error (spec 12 §7's copy deck), and their own uploads are in the album the whole time.
3. **Account linking (optional, durable).** Firebase anonymous → email-OTP/Google account linking upgrades the uid without losing data. Offered, never required. Built on the host side (§1: a one-tap Google card on the wizard's success screen, `linkWithPopup` on the existing uid, claims untouched) because a host's loss is the whole event; a guest's is one album that a magic link already recovers.

## 4. Consent model — three rings, collected at natural moments

```
Ring 0  SELF-ONLY      only the uploader's uid sees it
Ring 1  EVENT POOL     host console + face-matched subjects' private albums   ← DEFAULT
Ring 2  PUBLIC         public gallery + kiosk + public reels/collages         ← opt-in + gates
```

**"Public" here is a ring, not an audience size — do not conflate it with `access.mode`.** Ring 2 says *which of this event's surfaces* an item may reach; `access.mode: open | invite` (spec 08 §3) says *how many people the event has*. They are orthogonal axes and they compose: a Ring-2 photo at an invite-only event is on that event's wall and readable by that event's members, and by nobody else. `recompute_visibility` (spec 04 §2) keeps exactly the inputs listed here — no media document ever gains an audience field, and the boundary is enforced entirely by membership (spec 04 §2).

**When consent is collected (all moments, exhaustively):**

| Moment | What is asked | Granularity |
|---|---|---|
| Join screen | One sentence + link: "Photos you share go to the couple's album and to people who appear in them. Public display is always your choice per upload." | Event-level notice (not a wall of text) |
| Every upload batch | Toggle: **"Show in the big-screen public gallery"** (default OFF) + collapsed "keep just for me" (Ring 0) | **Per batch** — the unit people think in |
| After upload | Padlock chip on each photo in "my uploads" — tap to flip Ring 2↔1↔0 anytime | **Per photo override, retroactive** |
| Selfie enrollment | Explicit biometric consent screen: what the selfie is used for (finding *your* photos in *this* event), retention (event + 30 days), delete button location. Checkbox, not pre-ticked. | Per person |
| Appearing in a public photo | **Subject veto:** in your private album, any photo of you carries "hide me from public" → sets `subjectVetoes[personId]` → instantly demoted to Ring 1 | Per photo per subject |

**Retroactivity is non-negotiable:** consent changes call `recompute_visibility(mediaRef)` (spec 04 §2) in the same transaction; kiosk and public gallery drop the item within seconds via their listeners. Reels already rendered with a now-vetoed photo: reel is unpublished from kiosk playlist and queued for re-render without that asset (spec 06 §7).

**Why per-batch default + per-photo override (design rationale):** per-photo consent dialogs for 30 photos = consent fatigue = users mash "yes" = worthless consent. Per-portfolio (one-time) consent is the opposite failure — people share differently at the Sangeet than at the Pheras. Per-batch matches the mental model ("these ones from just now"), and the padlock override keeps per-photo control without a modal in the hot path.

## 5. Deletion & data lifecycle

- **Delete my data** (settings): deletes person doc + embeddings + reactions, unlinks uids, sets `deleted=true` on their uploaded media (tombstone; objects lifecycle-deleted), removes them from face indexes. Public reels containing their *uploads* are re-rendered; their *face* in others' photos: face doc deleted → drops out of albums.
- Event end + 30 days: bucket lifecycle rules purge raw/derived; Firestore TTL on event subtree. Stated in README (data-sovereignty judging point).
- GPS EXIF stripped at intake (spec 01 §5). Free-tier Gemini is never used for guest media (training-data policy).

## 6. Google Photos export ("get these into my camera roll")

Tiered, because the Photos API surface shifted in 2025 (Library API scopes were restricted; **verify current status of `photoslibrary.appendonly` + `mediaItems:batchCreate` for app-created content before building — Day-1 check, 30 min**):

- **P0 — Web Share / download:** private album → "Save" → Web Share API with files (iOS/Android share sheet → "Save Image", multi-select) + zip download fallback (Cloud Run streams a zip of signed-URL reads). Works everywhere, demoable.
- **P1 — "Send to Google Photos" (if API check passes):** button → Google OAuth (incremental, `appendonly` scope) → Cloud Run Job streams originals from GCS → Photos upload endpoint → `batchCreate` into an album named after the event → deep link to the album. This is a judge-pleasing Google-ecosystem moment; build only after the API check.
- **Fallback narrative if API is closed:** "Export to Google Drive" (Drive API is open) — same UX shape.

## 7. API surface added by this spec

```
POST /v1/events/{eventId}/people                    # enroll: {displayName, selfie (base64)} → held claim (+consent recorded)
POST /v1/events/{eventId}/claim-links               # → magic link (current uid/person)
POST /v1/claim                                      # {code} → custom token
POST /v1/events/{eventId}/people/reclaim            # {selfie} → held claim | 403 (ambiguous / banned / no consent)
POST /v1/events/{eventId}/media/{id}/consent        # {ring} (uploader only; rules-enforced)
POST /v1/events/{eventId}/media/{id}/subject-veto   # (matched subjects only)
GET  /v1/events/{eventId}/claims?status=            # host-authed: the review queue (held | granted | denied)
GET  /v1/events/{eventId}/claims/{claimId}/selfie   # host-authed: 302 → signed URL of the review selfie
POST /v1/events/{eventId}/claims/{claimId}/review   # host-authed: approve|deny a held claim (§3)
POST /v1/events/{eventId}/claims/{claimId}/reverse  # host-authed: undo an approval granted by mistake (§3)
DELETE /v1/events/{eventId}/people/me               # full deletion flow
POST /v1/events/{eventId}/export/google-photos      # P1
```

## 8. Acceptance criteria

- [ ] New browser, QR scan → uploading within 2 taps; no name/email ever demanded.
- [ ] Photos from a never-enrolled uid appear in host console + matched subjects' albums, and **never** on kiosk without the batch toggle.
- [ ] Magic link opened in a second browser shows the same private album; revoking it kills access.
- [ ] Selfie re-claim on a fresh device lands in the host queue and recovers the album on approval; a non-matching face is rejected; two candidates inside the ambiguity margin are refused outright (403), never queued; the original session sees the "new device" notice.
- [ ] Flipping a photo to Ring 0 removes it from host console + subject albums within 5 s; subject veto pulls a kiosk photo within 5 s.
- [ ] Delete-my-data leaves no face docs, no embeddings, no reactions, and tombstoned media.
- [ ] **Every** enrollment writes zero `personId` links until host approval; approval links all retroactively; denial leaves the cluster unclaimed — both outcomes recorded in `claimAudits`. The `CLAIM_REVIEW_THRESHOLD` crossing changes only the card's `holdReason` (`CLAIM_SIZE` vs `HOST_APPROVAL`), never whether the claim is held.
- [ ] A held claim grants nothing: no `personId` custom claim on the token, no `uidLinks` entry, no `personId` on any face doc — while the enrollee's *own* uploads stay visible to them throughout (`uploaderUid` path).
- [ ] `worker-face` never auto-links a new face to a person whose `claimApproved` is `false`: an enrollment left pending across several uploads that match it at `tau_match` accrues zero album items until the host approves.
- [ ] Denying an enrollment that created its person leaves no person doc, no `enrollments/{personId}` template, no `private/` subdocument and no stored review selfie, and the `personId` claim is revoked; denying a *re-claim* leaves the target person, their template and their album byte-for-byte unchanged.
- [ ] Every claim (enroll/re-claim/magic-link) produces a `claimAudits` entry visible in the host activity feed; the held queue is readable via `GET …/claims?status=held` (a held claim is never unresolvable); `POST …/claims/{claimId}/reverse` reverses a granted claim and faces return to unclaimed state.
- [ ] A second `POST /v1/events` by the same host uid leaves the first event's console fully accessible (`hosts` is an array, not a scalar); a token still carrying the legacy scalar `host` claim keeps working.
