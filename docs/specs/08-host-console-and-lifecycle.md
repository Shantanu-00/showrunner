# Spec 08 — Host Console & Event Lifecycle (start / stop / panic)

Goal: the host is the only human operator this system has. Everything they can do — creating, starting, pausing, killing, and wrapping an event — is defined here. This is also where "is there a start/stop button for the entire system?" is answered: **yes, the event status field IS the system's master switch**, and everything else derives from it.

## 1. Host authentication (decided)

- `POST /v1/events` (unauthenticated create, rate-limited) → creates event in `draft` → returns a **host magic link** + a printable recovery code. Redeeming it (same claim-link machinery as spec 02 §3) mints a custom token whose claims include `host: {eventId}`.
- Co-hosts: host console can issue more host links; all revocable. Losing all host devices → recovery code.
- Production note for README: swap redemption for Google Sign-In; claims structure unchanged.

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

## 3. Event creation wizard (draft state)

1. **Details:** name, date(s), **timezone (required — EXIF interpretation depends on it, spec 03 §5.1)**.
2. **Event Type Profile (spec 11 §2):** host picks a template (Wedding — Generic/Hindu/Christian, Bachelor(ette) Party, Birthday, Graduation, Corporate Offsite, Custom) → wizard pre-fills `vipTopology` + `sensitivityProfile` dials (PDA/alcohol/attire) + `culturalGlossary` from that template, all shown as **editable, not silently authoritative** — same discipline as the timeline review one step later. This single choice is what makes the rest of the pipeline behave correctly for *this* event without any hardcoded per-culture branch anywhere in the codebase.
3. **Timeline paste → parse → REVIEW:** host pastes unstructured itinerary (pre-filled with the template's `requiredMomentsTemplate` as a starting table) → Model Armor sanitize → Gemini structured parse merges in → **editable review table** (stage names, windows, required moments, VIP tags). The host confirms or fixes before anything downstream trusts it — an LLM parse of a WhatsApp itinerary forward is never silently authoritative. Re-parse and manual add both supported.
4. **VIP enrollment & tiering (spec 11 §3):** upload reference photos per VIP → face pipeline enrolls them as protected persons (impersonation guard, spec 02 §3), each assigned a `tier` (0–2). Under `vipTopology: pyramid` the wizard defaults every enrolled person to tier 3 and the host promotes explicitly; under `flat` it offers a one-tap "mark everyone as inner circle" bulk action and the host demotes exceptions instead. Guest self-enrollment (spec 02) always defaults to tier 3 regardless of topology — this step only sets the *host-enrolled* default.
5. **QR + links:** printable guest QR (deep link with eventId), kiosk URL, host magic links.

## 4. Console surfaces (single Next.js route `/host`, tabbed)

| Tab | Contents |
|---|---|
| **Live ops** | Stage override ("Now: ▶ Pheras" — always wins, spec 05 §2), director suggestion cards (stage-advance proposals), **Run director now**, upload velocity, active guests |
| **Review queue** | `host_review` verdicts (incl. `minor_prominent`) + `blocked` moderation area; approve/reject writes the audited verdict |
| **Coverage** | The ledger visualized: per stage × moment × VIP heat grid — *the Story Director's eyes, visible to judges* |
| **Bounties** | Active/fulfilled/expired list, manual bounty compose, escalate/cancel |
| **People** | Every enrolled person, their `tier` (editable inline), and a **"Feature this person"** toggle (spec 11 §3.5) — an audited *ranking* override (next hero slot / next reel candidate set) that can never surface a photo `recompute_visibility` hasn't already made public; the symmetric opposite of the per-item yank below |
| **Media** | Full archive (host reads all non-deleted), per-item visibility override, ops alerts (quarantines/DLQ) with replay buttons |
| **Director prefs** | The free-text preference box → Memory Bank (spec 07 §4) + thumbs on reels/kiosk items |
| **Controls** | Go Live / Pause / Wrap, **PANIC: Freeze public** (§5), host links management, wrap-up report |

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
