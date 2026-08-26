# Spec 02 — Identity, Consent & Privacy

Goal: zero-friction entry (scan QR → uploading in 10 seconds, no account), yet every photo has an owner, consent is never ambiguous, a guest can recover their private album from any device, and privacy defaults protect people who never touch a settings screen.

## 1. Identity model — three layers, loosely coupled

| Layer | What it is | Lifetime |
|---|---|---|
| **`uid`** (Firebase Anonymous Auth) | A browser session identity, created silently on first open. Every upload/reaction is stamped with it. | Per browser profile; lost if storage cleared |
| **`personId`** (`events/{eventId}/people/{personId}`) | A *human* at the event: display name, optional selfie embedding, taste profile, VIP flag. Created at selfie enrollment, name entry, or by the host (VIPs). | Per event |
| **`guestLink`** (`uidLinks` array on person doc) | The join table: which uids belong to which person. A person can have many uids (phone + laptop + re-scan). | Per event |

Guests are **never "verified"** in the account sense — and don't need to be. Uploading requires only a uid. Identity (personId) is only needed to *receive* things: private album, bounty points leaderboard name, personalized reel.

**Custom claims make identity enforceable in security rules.** When a uid links to a person (enrollment, magic link, re-claim), the server sets Firebase custom claims `{personId, host?}` via the Admin SDK and the client force-refreshes its ID token. Firestore rules then read `request.auth.token.personId` / `request.auth.token.host` directly — no `get()` joins per read (rules allow only 10 and they're billed). This is the mechanism behind every rule in spec 04 §2.

**Host identity (decided):** creating an event returns a **host magic link** + printable recovery code — the same claim-link machinery as guests, but redemption sets the `host: {eventId}` custom claim. Multiple co-hosts = multiple redemptions; links revocable from the console. README production note: swap for Google Sign-In; the claim structure is unchanged. Full host console + lifecycle: spec 08.

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
2. **Selfie re-claim.** If the person enrolled a selfie: new session takes a selfie → embedding → `findNearest` against `people` (this event only) → match ≥ **strict** threshold τ_claim (higher than the photo-matching threshold) **with an ambiguity margin**: if the top-2 matches are within 0.08 of each other (twins and siblings are common at weddings), auto-claim is declined and the UI falls back to magic link / host assist. Anti-abuse: selfies must come from a **live camera capture** (`getUserMedia` / `capture` attribute — no gallery picker), on re-claim a notice event is written to that person's album ("new device joined — not you? tap here"), and VIP persons additionally require host approval. (Honest README note: no liveness detection in the hackathon build; production would add it.)

**Enrollment impersonation guard (first enrollment, not just re-claim):** a *new* enrollment whose selfie matches an **existing enrolled person or a VIP reference face** ≥ τ_claim is never silently created as a second person — VIP match → host-approval queue; non-VIP match → treated as a re-claim of that person (same margin rule). Without this, anyone could enroll with a photo of the bride and receive her entire private album. Matching an *unclaimed* face cluster is the normal, desired path (the claim operates at the **face level** — `findNearest` over face embeddings, claiming all matches ≥ τ_claim regardless of `clusterId`, per spec 03 §5.2 — which subsumes the cluster and is immune to transient duplicate clusters).

   **Claim-integrity hardening for the unclaimed-cluster path (the residual the guards above don't cover):** the photo-of-a-photo vector — someone live-captures a printed or displayed photo of a *never-enrolled* guest and claims their unclaimed faces. No liveness detection exists in the hackathon build (disclosed above), so three cheap policy layers bound the damage instead:
   1. **Claim-size gate.** If a *first-time* enrollment's face-level claim would link ≥ `CLAIM_REVIEW_THRESHOLD` faces (default **8**, env-configurable), the links are **held, not applied**: no `personId` is written to any face doc, a review card lands in the host console (the enrollment selfie beside 4 cluster exemplars — a five-second visual check), and the enrollee sees "the host is confirming it's you — your own uploads are already in your album." Approve → all links apply retroactively in one batch; deny → the enrollment stands as a new person with zero claimed faces, flagged in the audit trail. Rationale: a heavily-photographed person is either already host-enrolled (protected by the guards above) or socially central — exactly the album worth stealing; a sub-threshold claim is low-stakes and stays frictionless. (Judge-tour note: a judge's own face matches zero seeded faces, so the tour never touches this gate.)
   2. **Claim audit + host visibility.** Every successful claim, any size, writes `claimAudits/{claimId}: {personId, uid, faceCount, topSimilarity, method: enroll|reclaim|magic_link, at}` and surfaces in the host console activity feed — a wrong claim is visible and host-reversible (unlink → faces return to unclaimed), never silent.
   3. **Inner-circle invite links.** The creation wizard (spec 08 §3, VIP step) offers **"family links"**: named, personId-bound magic links (the §3.1 machinery, nothing new) the host sends over WhatsApp to tier 0–2 people and anyone else they name. Those people never need the selfie path at all — and since host-enrolled persons already require host approval to be claimed, the inner circle is fully off the attackable surface.
3. **Account linking (optional, durable).** Firebase anonymous → email-OTP/Google account linking upgrades the uid without losing data. Offered, never required.

## 4. Consent model — three rings, collected at natural moments

```
Ring 0  SELF-ONLY      only the uploader's uid sees it
Ring 1  EVENT POOL     host console + face-matched subjects' private albums   ← DEFAULT
Ring 2  PUBLIC         public gallery + kiosk + public reels/collages         ← opt-in + gates
```

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
POST /v1/events/{eventId}/people                    # enroll: {displayName, selfie (base64)} → personId (+consent recorded)
POST /v1/events/{eventId}/claim-links               # → magic link (current uid/person)
POST /v1/claim                                      # {code} → custom token
POST /v1/events/{eventId}/people/reclaim            # {selfie} → custom token | 403
POST /v1/events/{eventId}/media/{id}/consent        # {ring} (uploader only; rules-enforced)
POST /v1/events/{eventId}/media/{id}/subject-veto   # (matched subjects only)
POST /v1/events/{eventId}/claims/{claimId}/review   # host-authed: approve|deny a held claim (claim-size gate, §3)
DELETE /v1/events/{eventId}/people/me               # full deletion flow
POST /v1/events/{eventId}/export/google-photos      # P1
```

## 8. Acceptance criteria

- [ ] New browser, QR scan → uploading within 2 taps; no name/email ever demanded.
- [ ] Photos from a never-enrolled uid appear in host console + matched subjects' albums, and **never** on kiosk without the batch toggle.
- [ ] Magic link opened in a second browser shows the same private album; revoking it kills access.
- [ ] Selfie re-claim on a fresh device recovers the album; a non-matching face is rejected; the original session sees the "new device" notice.
- [ ] Flipping a photo to Ring 0 removes it from host console + subject albums within 5 s; subject veto pulls a kiosk photo within 5 s.
- [ ] Delete-my-data leaves no face docs, no embeddings, no reactions, and tombstoned media.
- [ ] An enrollment that would claim ≥ `CLAIM_REVIEW_THRESHOLD` faces writes zero `personId` links until host approval; approval links all retroactively; denial leaves the cluster unclaimed — both outcomes recorded in `claimAudits`.
- [ ] Every claim (enroll/re-claim/magic-link) produces a `claimAudits` entry visible in the host activity feed; host "unlink" reverses it and faces return to unclaimed state.
