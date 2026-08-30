# Spec 08 — Host Console & Event Lifecycle (start / stop / panic)

Goal: the host is the only human operator this system has. Everything they can do — creating, starting, pausing, killing, and wrapping an event — is defined here. This is also where "is there a start/stop button for the entire system?" is answered: **yes, the event status field IS the system's master switch**, and everything else derives from it.

## 1. Host authentication (decided)

- `POST /v1/events` (unauthenticated create, rate-limited) → creates event in `draft` → returns a **host magic link** + a printable recovery code. Redeeming it (same claim-link machinery as spec 02 §3) adds the event to the caller's **`hosts` array claim**. An array, not the scalar `host` this originally specified: the scalar meant creating a second event silently revoked access to the first, which is a data-loss bug wearing a claim's clothing. Roughly 32 events fit in the 1000-byte claims budget; the server refuses the 33rd rather than truncating. The legacy scalar is still honoured so a console open mid-event survives the change.
- **Creation stays anonymous and unauthenticated — there is deliberately no signup wall.** Google is an *optional* one-tap upgrade on the wizard's success screen (spec 02 §1: `linkWithPopup` preserves the anonymous uid and its claims), offered beside the recovery code, and declining costs nothing. What it buys is the tail risk on the other side: the recovery code is shown once, and a host who loses it and clears their browser has lost the event.
- **"I already have an event" lives at `/host`, on a bare recovery code.** `POST /v1/host-claim` takes a code with no eventId and answers with the event. This fixes a closed loop, not a convenience gap: the code box previously existed only inside `/host/{eventId}`, so reaching the box required already knowing the id that the code is what identifies.
- Co-hosts: `POST /v1/events/{eventId}/host-links` issues more host links; `GET …/host-links` lists them and `POST …/host-links/{linkId}/revoke` kills one. The list returns **metadata only — no URL, no code** — because only hashes are ever stored; a link that could be re-read from the console would be a link the console could leak. Losing all host devices → `POST …/recovery-code` re-mints one, revoking the previous.
- **Kiosk links are not host links.** `POST …/kiosk-links` grants `members` and never `hosts` (spec 04 §4): a venue TV in a public room is the last device that should be able to wrap the event.

## 2. Event lifecycle — the master state machine

```
draft ──[Go Live]──► live ──[Pause uploads]──► paused ──[Resume]──► live
                       │                                              │
                       └───────────────[Wrap event]──────────────────┴──► wrapping ──► wrapped
```

**Everything keys off `event.status` — no per-event infrastructure exists:**

| Status | Uploads (signed URLs) | Director ticks | Kiosk | Guest app |
|---|---|---|---|---|
| `draft` | ✗ (403) | ✗ (tick skips) | setup screen | "event hasn't started" |
| `live` | ✓ | ✓ | full show | full |
| `paused` | ✗ (banner: "uploads paused") | ✓ (can still direct/render from existing media) | full show | view-only + banner |
| `wrapping` | grace period: in-flight outbox items accepted 30 min, no new intents | final-cut sequence (below) | finale mode | view + download |
| `wrapped` | ✗ | ✗ | finale loop / off | albums remain until retention purge |

The global Cloud Scheduler tick (spec 05 §1) queries `status=='live' or 'wrapping'` — going live or wrapping is purely a Firestore status flip. **This is the start/stop button.**

### Go Live checklist (enforced, not vibes)
The button enables only when: timeline reviewed (§3), ≥1 stage, event.timezone set, QR generated, kiosk "Start show" tested (optional warning), **and platform capacity available (spec 11 §1 — a transactional `platform/liveEventCount` gate, default cap 3 concurrent live events, refused with a "contact the developer" message beyond that; the console badge greys out proactively but the server transaction is the real gate)**. Going live stamps `liveAt`, increments the capacity counter, and arms the tick eligibility.

### Wrap sequence (autonomous finale — a demo moment)
1. Status → `wrapping`; guests see "last call — uploads close in 30 min".
2. Grace period ends → uploads closed; Story Director runs a **final tick**: expires open bounties, commissions final-cut versions of every reel persona + any missing `stage_recap`s.
3. All renders published → **wrap-up report** generated (3.7-flash): coverage summary per stage, honest gaps ("no photos of the priest during Kanyadaan — bounty expired unfilled"), top contributors, stats. Written to the host console + kiosk finale slide ("2,314 photos · 11 reels · 47 photographers — thank you ❤").
4. Status → `wrapped`, in the same transaction that decrements `platform/liveEventCount` (spec 11 §1 — the slot is freed the instant the event stops actually consuming resources, not before). Retention clock (spec 02 §5) starts.

## 3. Event creation wizard (draft state) — **revised by spec 13: itinerary-led, not template-led**

1. **Details:** name, **date range** (`startDate`/`endDate`, local dates — spec 13 §1), **timezone
   (required — EXIF interpretation depends on it, spec 03 §5.1)**, **expected participants**
   (spec 13 §1 — feeds group coverage and the invite seat default), and **access mode** (open /
   invite; invite mints the join code inside the creation request and shows it exactly once).
2. **Itinerary — paste / PDF / screenshot → parse → REVIEW (spec 13 §3):** Model Armor sanitize →
   Gemini structured parse → **editable, day-grouped review table** (stage names, windows —
   prefilled from the parse's dated proposals, required moments, theme, expected setting). The
   host confirms or fixes before anything downstream trusts it — an LLM parse is never silently
   authoritative, whatever it was parsed from. Re-parse and manual add both supported; skippable.
3. **People (spec 13 §7):** optionally add participants with reference photos
   (`POST …/people/host-enroll` — the host is the approver, no uid link is ever created here)
   and tiers per spec 11 §3's topology defaults. Guest self-enrollment (spec 02) always defaults
   to tier 3 regardless.
4. **QR + links:** printable guest QR (deep link with eventId, `?joinCode=` in invite mode),
   kiosk URL, host magic links, the recovery code, the once-only invite code.

**The Event Type Profile (spec 11 §2) is no longer a wizard step.** Every event is created
`custom` (neutral dials, empty glossary); the templates survive as *presets* behind a quiet
select in the console's **Settings panel**, host-reviewed as ever. Mutability split: the
sensitivity dials are editable while `draft|live|paused` (they are ceilings — tightening
mid-event is a safety action, and verdicts are stored per-photo so loosening only affects future
ones); `culturalGlossary`/`requiredMomentsTemplate`/`vipTopology` stay draft-only (they feed
per-photo prompts and bounty arming).

### Access mode — who may read this event

```
events/{eventId}.access: {
  mode: 'open' | 'invite',   # who may become a member
  maxGuests,                 # seat cap; null = uncapped. Default INVITE_DEFAULT_SEATS = 300
  codeHash,                  # sha256 of the invite code — the code itself is never stored
  codeRotatedAt,
  kioskPublic,               # host's "don't put this event on a wall at all" switch
}
events/{eventId}.guestCount  # seats taken, incremented transactionally in join
```

**This is a different axis from the consent rings, and the distinction is worth stating plainly because both vocabularies use the word "public".** Ring 2 (spec 02 §4) says *which of this event's shared surfaces* a photo may reach; `access.mode` says *how many people the event has*. They compose, and neither is expressed in the other: a Ring-2 photo at an invite-only event is on that event's wall and readable by that event's members, and by nobody else. `recompute_visibility` (spec 04 §2) keeps exactly its existing inputs and remains the single writer of `media.visibility` — **no media document gains an audience field**, and the boundary is carried entirely by the `members` claim.

- **Membership is minted, not asserted:** `POST /v1/events/{eventId}/join` writes `guests/{uid}` and adds the event to the caller's `members` claim (spec 04 §2). In invite mode the request must carry a code matching either `access.codeHash` or a live kiosk/host link hash.
- **Invite codes reuse the hashed-code machinery already in the file** — the same `_code_hash` / link-minting path as host links and album claim links, now its third instance. Only the hash is stored, so a lost code is **rotated (`POST …/access/code`), never recovered**. Same reason `GET …/host-links` returns no codes.
- **Seats are counted transactionally**, alongside the `guestCount` increment, in the pattern `_go_live_txn` already uses for `platform/liveEventCount`. **It counts uids, not humans** — spec 02 §1 gives one person many uids — which is why every human-facing string says "seats" and why the default is deliberately generous (300, `None` legal for uncapped, raising it one host tap). The failure this guards is a link leaking onto a group chat, not a guest list running one over: **a refused legitimate guest standing at the venue is a far worse outcome than one admitted stranger.**
- **Uploads require membership only in invite mode.** An open event's first photo must not wait on a join round trip. In invite mode the unauthenticated public byte paths in `api/media.py` and `api/reels.py` require membership too; no token scheme was needed, because the client already fetched non-public tiers authed and handed back a blob URL.
- **`invite → open` requires an explicit confirmation; `open → invite` is free.** The server answers the unconfirmed flip with `409 CONFIRM_REQUIRED` and returns the consequence copy **verbatim**, so a client cannot show softer wording than the server demands: *"Photos your guests already shared become reachable by anyone who joins this event's link. Nothing already private becomes public, and each guest keeps their per-photo padlock — but the door stops asking for a code."* Either direction writes an `ops/` audit entry, because "who could see this event" is exactly the change a host needs to be able to point at afterwards. Guests see a banner; their remedy is the retroactive per-photo padlock that already exists (spec 02 §4), not a new mechanism.
- **`kioskPublic` is a host preference, not a security boundary,** and is labelled as such in the console: `events/{eventId}/kiosk/{document}` stays world-readable (spec 04 §2's stated residual — a rule cannot consult the event document without a `get()`), so this switch is honoured by the kiosk client. What keeps a private event's wall dark to an outsider is that every collection the playlist points into is member-gated.
- Host endpoints: `POST …/access` (mode), `…/access/code` (rotate), `…/access/seats`, `…/access/kiosk`. Plus `POST /v1/events/join-code`, which resolves a bare invite code to `{eventId, eventName}` for the `/join` box — **a wrong code and a code for a nonexistent event return the identical error**, so the box is not an oracle for which events exist, and lookups are rate-limited per uid because every attempt is a Firestore query on an unauthenticated endpoint.

## 4. Console surfaces (single Next.js route `/host/{eventId}`, one scrolling column)

**One scrolling column, deliberately not tabbed.** The host operates this from a phone at the venue (spec 12 §5.4), and a tab bar's whole function is to hide seven eighths of the surface — including, at the moment it matters, the panel with a guest waiting in it. Ordering is the affordance instead: whatever is blocking someone sits highest. The two panic-critical controls (**Freeze Public**, **Run director now**) are pinned in a persistent header so neither is ever a scroll away.

| Panel | Contents |
|---|---|
| **Live ops** | Stage override ("Now: ▶ Pheras" — always wins, spec 05 §2), director suggestion cards (stage-advance proposals), **Run director now**, upload velocity, active guests |
| **Claim review** | The pending-album queue (`GET …/claims?status=held`, spec 02 §3): enrollment selfie beside 4 cluster exemplars, Approve / Deny, plus reverse on an approval given by mistake. Sits directly under the lifecycle KPIs — a held claim is a guest standing at the door, and every other panel can wait |
| **Access & seats** | Mode (open / invite-only, with the confirmation gate on `invite → open`), seats taken vs cap, the invite code with rotate, `kioskPublic`, co-host + kiosk links (metadata only), recovery code re-mint — §3 |
| **Review queue** | `host_review` verdicts (incl. `minor_prominent`) + `blocked` moderation area; approve/reject writes the audited verdict |
| **Coverage** | The ledger visualized: per stage × moment × VIP heat grid — *the Story Director's eyes, visible to judges* |
| **Bounties** | Active/fulfilled/expired list, manual bounty compose, escalate/cancel |
| **People** | Every enrolled person, their `tier` (editable inline), and a **"Feature this person"** toggle (spec 11 §3.5) — an audited *ranking* override (next hero slot / next reel candidate set) that can never surface a photo `recompute_visibility` hasn't already made public; the symmetric opposite of the per-item yank below |
| **Media** | Full archive (host reads all non-deleted), per-item visibility override, ops alerts (quarantines/DLQ) with replay buttons |
| **Director prefs** | The free-text preference box → Memory Bank (spec 07 §4) + thumbs on reels/kiosk items |
| **Controls** | Go Live / Pause / Wrap, **PANIC: Freeze public** (§5), wrap-up report (link management moved to Access & seats, where the rest of "who can reach this event" lives) |

## 5. Panic controls (the kill switches)

- **Freeze public (big red button):** one write — `event.publicFrozen=true`. The kiosk playlist collapses to a safe slate (couple monogram + schedule); public gallery queries return empty; `recompute_visibility` treats frozen as "Ring 2 suspended" (items keep their state; unfreeze restores instantly). Use case: anything inappropriate on the big screen, uncle complaints, speeches in progress. **Acceptance: frozen ≤ 2 s from tap.**
- **Per-item yank:** any kiosk/gallery item → "remove from public" (visibility override, audited) ≤ 5 s.
- **Pause uploads:** §2 — stops intake at the source (URL issuance) without touching the show.
- **Revoke a guest:** sets `guests/{uid}.banned=true` → URL issuance 403, listeners drop their pending items from public surfaces; media kept for host (abuse evidence) unless deleted.

## 6. Acceptance criteria

- [ ] Full lifecycle walk: create → parse+review timeline → go live → upload works → pause → uploads 403 while kiosk keeps playing → resume → wrap → grace period honored → final reels render → report appears → wrapped state read-only.
- [ ] Director ticks provably skip `draft`/`paused→wrapped` transitions per the table (log assertions).
- [ ] PANIC freeze clears kiosk + public gallery ≤ 2 s; unfreeze restores identical state (no recomputation drift).
- [ ] A garbage itinerary paste produces an editable (wrong) table, not a live event with a hallucinated timeline — Go Live is blocked until reviewed.
- [ ] Host magic link redeems to a working console; revoked link is dead; recovery code issues a fresh one.
- [ ] Banned guest: cannot obtain URLs; their public items vanish; their private data intact pending host decision.
- [ ] Go Live at platform capacity (spec 11 §1) is refused with the capacity message, not a silent failure; wrapping any one of the capacity-holding events immediately frees a slot for a new Go Live.
- [ ] Selecting the `bachelor_bachelorette` template at creation defaults every subsequently-enrolled VIP to tier 1 without individual promotion; a `wedding_hindu` selection defaults to tier 3 until explicitly promoted (spec 11 §3.2).
- [ ] Creating a second event from the same host uid leaves the first event's console fully usable — both events are in the `hosts` claim, neither revoked the other.
- [ ] A host who has only a recovery code and no eventId reaches their console from `/host` alone; re-minting a recovery code kills the previous one.
- [ ] `GET …/host-links` never returns a URL or a code (response-shape assertion), and a revoked link's redemption fails.
- [ ] Invite-only event: a uid that has not joined reads nothing and cannot obtain an upload URL; joining with the correct code grants membership and both work; the same call with a wrong code fails. On an **open** event the first upload succeeds with no prior join call.
- [ ] Seat cap: with `maxGuests` reached, the next `POST /join` is refused with the seats message and `guestCount` does not increment; raising the cap by one host call admits the next guest immediately.
- [ ] Rotating the invite code stops the old code working on the next `POST /join`; nothing in any response or console surface ever echoes a stored code back.
- [ ] `invite → open` without `confirm` returns `409 CONFIRM_REQUIRED` carrying the consequence copy verbatim and does **not** change the mode; with `confirm` it flips and writes an `ops/` entry. `open → invite` needs no confirmation. Neither direction rewrites any `media.visibility`.
- [ ] `POST /v1/events/join-code` returns the identical error for a syntactically valid code that matches nothing and for a code belonging to a deleted event — the endpoint is not an existence oracle — and is rate-limited per uid.
- [ ] A kiosk link grants `members` and not `hosts`: the kiosk session can read the playlist and its media but cannot pause, wrap or freeze the event.
