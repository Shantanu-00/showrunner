# Spec 12 — Frontend Design System ("Grand Ballroom at Midnight")

Goal: the design contract for all four surfaces — guest PWA, kiosk, host console, Flight Deck — written *before* the frontend build so every screen is built once, to spec, instead of restyled in a Day-4 "luxury pass". This is the engineered artifact behind the **Best Multimodal UX** prize rung (PLAN §1): the kiosk cinema, the bounty choreography, and the event-adaptive theming *are* the multimodal UX story, and they only score if they are designed, legible, and honest. Consumed by every frontend route; extends spec 04 (kiosk visuals), spec 05 (bounty surfaces), spec 07 (swipe), spec 08 (console). No new backend mechanism anywhere in this spec — it styles and *places* what other specs already compute.

## 1. Design philosophy (three audiences, one system)

| Audience | What they judge | Design consequence |
|---|---|---|
| **DevRel judge** | "Could I put this frame in a keynote / show a customer?" | 3 hero frames engineered deliberately: the wizard theme-flip, the bounty mission on a phone, the reel premiere takeover |
| **Architect judge** | Distrusts polish; looks for honesty | Truthful live counters only (never a fabricated number), glass-box "Why this photo?" overlay, demo conveniences disclosed on-screen |
| **Video viewer (likely muted, 2×)** | Can they follow it from pixels alone? | Every beat self-captions; big type; motion readable at a glance; **zero spinners anywhere in the unedited take** |

**Principles (each one is checkable):**
1. **The show, not chrome.** Kiosk and reels are full-bleed; UI recedes; content is the interface.
2. **State made visible.** Pipeline states (`uploading → curating → live`) appear *in the product*, not only in the Flight Deck — the rubric's "state management" argued visually.
3. **Consent as ritual, not boilerplate.** Consent controls live exactly where intent forms (§5 maps all four moments), designed as moments a privacy-minded judge would screenshot.
4. **Two visual languages, one system.** Guest surfaces = editorial luxe; ops surfaces (Flight Deck, console internals) = mission control. The contrast is a deliberate statement: two audiences, two languages — say it in the video.
5. **Truthful by construction.** Any number on screen traces to a real Firestore aggregate or OTel-derived value. If a counter can't be real, it doesn't ship.
6. **No chat window anywhere.** Every screen either *shows state* or *asks for exactly one tap*. No free-text input exists outside host setup (itinerary paste, names, director prefs) and captions.

## 2. Design tokens (single source: `frontend/styles/tokens.css`)

All values are CSS custom properties on `:root`, overridden by `[data-theme]` and `[data-stage]` blocks (§3). Tailwind v4 consumes them via `@theme`; no hardcoded hex anywhere else in the codebase (grep-auditable).

```css
:root {
  /* base canvas — dark-first: venue kiosks live in dim rooms, photos pop on near-black,
     and dark UIs read dramatically better on video */
  --bg-0: #0B0709;            /* app background, maroon-black undertone */
  --bg-1: #171013;            /* raised surface */
  --bg-glass: rgb(23 16 19 / 0.72);   /* + backdrop-filter: blur(16px) */
  --maroon-900: #3D0C11;  --maroon-700: #5C1620;  --maroon-500: #7A1E2B;
  --gold-500: #D4AF6A;    --gold-300: #E9CF9A;    /* accent + accent-soft defaults */
  --ivory: #F5EFE6;           /* primary text */
  --ink-muted: #A99E92;       /* secondary text */
  --ok: #7FB069;  --warn: #E0A458;  --danger: #C0392B;
  --hairline: 1px solid rgb(212 175 106 / 0.35);   /* gold hairline borders */
  --radius-card: 12px;  --radius-banner: 20px;  --radius-pill: 999px;
  --space: 4px;               /* 4px grid; all spacing = multiples */
  --dur-micro: 150ms;  --dur-standard: 240ms;  --dur-cinematic: 700ms;
  --ease-luxe: cubic-bezier(0.22, 1, 0.36, 1);
}
```

**Typography** (all self-hosted via `next/font` — OFL-licensed, no third-party CDN):
- **Display:** Fraunces (600/700, optical sizing on) — titles, kiosk captions, premiere cards.
- **UI:** Inter — everything interactive; `font-variant-numeric: tabular-nums` on **every** live counter so numbers never jitter on camera.
- **Mono:** JetBrains Mono — Flight Deck numerals, cost ticker, IDs.
- Type scale, 1.25 ratio: 12 / 14 / 16 / 20 / 25 / 31 / 39 / 49 / 61px. Kiosk minimums in §6.

**Texture:** one SVG-noise film-grain overlay, `opacity: 0.04`, **kiosk only** (guest PWA stays clean for legibility on cheap phones). Elevation via borders + glass, not heavy shadows.

## 3. Event-adaptive themes (the "any event" proof, in one shot)

`eventTypeProfile.templateId` (spec 11 §2) selects a theme; the active stage (spec 04 §4 already carries per-stage `theme`) overrides the accent within it. Mechanism: `<html data-theme="wedding_hindu" data-stage="haldi">`, pure CSS custom-property overrides — **no re-render, no reload**, which is exactly what makes the demo beat work: in the creation wizard, flipping the template retunes the entire product live on camera. One shot proves "any event, not a wedding app."

| templateId | Theme name | `--bg-0` | `--accent` | `--accent-2` |
|---|---|---|---|---|
| `wedding_hindu` (default) | **Baraat** | `#0B0709` | `#D4AF6A` gold | `#8C1D2F` crimson |
| `wedding_generic` / `_christian` / `_muslim` | Ballroom | `#0E0C12` | `#E9CF9A` champagne | `#B76E79` dusty rose |
| `birthday` | Candlelight | `#0B0E16` | `#F27059` coral | `#F2C14E` candy gold |
| `bachelor_bachelorette` | After Hours | `#070709` | `#9B5DE5` electric violet | `#00F5D4` acid mint |
| `graduation` | Procession | `#0A1024` | `#E3B23C` cord gold | `#8ECAE6` sky |
| `corporate_offsite` | Keynote | `#0C1117` | `#8ECAE6` ice | `#C9D1D9` silver |
| `custom` | inherits Ballroom | — | host-pickable accent (single color input, wizard) | derived |

**Stage accents, `wedding_hindu`** (spec 04 §4's "Haldi gold, Pheras crimson" made concrete): `haldi #E3B23C` turmeric · `sangeet #9B5DE5` violet · `pheras #8C1D2F` crimson · `reception #D4AF6A` gold-on-midnight. Stage change re-themes the kiosk ≤ 5 s (existing spec 04 acceptance) — visually: a `--dur-cinematic` crossfade of the accent, never a hard flash.

## 4. Motion language

- **Durations/easing:** micro-interactions `--dur-micro`, standard transitions `--dur-standard`, kiosk/cinematic `--dur-cinematic`, all on `--ease-luxe`. Kiosk slot crossfade: 800ms.
- **Ken Burns (kiosk heroes):** scale 1.04 → 1.12 across the slot's `holdSec`, pan vector pointed *away from* the primary face box center (never crop a head — the boxes come from Face Indexer; VO line: *"the layout engine knows where every face is"*).
- **`prefers-reduced-motion`:** every transform-based animation degrades to opacity fade; Ken Burns freezes to a static smart-crop. One README line about this = production-readiness signal.
- **The no-spinner rule (hard rule for every P0 surface):** no indeterminate spinner exists anywhere. Every wait state = branded skeleton shimmer (a slow gold sheen sweep, 1.6s loop) + **agent-verb micro-copy**:

| Wait state | Copy |
|---|---|
| Photo uploading | "Sending to the director…" |
| Curator pending | "The Curator is judging your shot…" |
| Face match pending | "Looking for you in the archives…" |
| Reel rendering | "The Reel Director is in the edit room…" |
| Kiosk cold start | "The show is about to begin" |

## 5. Screen map & user flows (the information architecture — where everything lives)

### 5.1 Route inventory (canonical; supersedes the sketch in PLAN §11)

```
/                          hosted-URL landing = judge-mode 60-second tour (spec 09 §4)
                           + a quiet "Create your own event" link → /host
/join/{eventId}            guest PWA shell (the QR target) — bottom tabs: Event | Camera | Me
                           (spec 04's `/gallery` and `/me` are the Event and Me TABS of this
                            one shell — one PWA, tabbed; never separate apps)
/kiosk/{eventId}           kiosk client — operator setup screen → "Start show" → fullscreen
/host                      event creation wizard (no event yet; spec 08 §3)
/host/{eventId}            host console, tabbed per spec 08 §4
/host/{eventId}/flightdeck Flight Deck (spec 10's `/host/flightdeck`, event-scoped;
                            `?present=1` = read-only presentation mode for filming)
/claim#<code>              magic-link redemption (spec 02 §3) → lands on /join Me tab
```

Every client (guest, kiosk, host) signs in with Firebase anonymous auth silently on load — the kiosk too, since media-doc reads require an authed event member (spec 09 §3 rules).

### 5.2 Guest journey, moment by moment (consent moments labeled C1–C4 = spec 02 §4's table, placed)

1. **Scan QR → `/join/{eventId}` welcome.** Event monogram over a blurred hero photo (or themed gradient pre-first-photo), one-line consent notice (spec 02 §4 join-screen row, verbatim) + single CTA **"Join the event"**. A quiet secondary link: **"Just browse"** → Event tab, view-only (anonymous auth suffices for public reads). Spec 02's acceptance holds: uploading within 2 taps of scanning, no name/email ever demanded.
2. **First-run sheet (one screen, never a carousel tour):** three cards — *Share photos* · *Find photos of you* (enroll) · *Earn points* — dismissible in one tap. The enroll card is an invitation, not a gate.
3. **Event tab (default landing = the public gallery).** Masonry grid of `public` photos animating in live; **stage filter chips** across the top (Haldi · Sangeet · …); a **Highlights** toggle (the vipWeight-ranked shared ordering, spec 04 §3); a horizontal **Reels row** (published public reels → vertical player); top bar: event name, 🏆 leaderboard entry (opens sheet), **Missions chip** ("🎯 2 active") when bounties are live (opens bottom sheet listing active bounties + my submissions/points).
4. **Camera FAB (center tab, always visible).** Tap → capture / multi-select → **the send sheet = consent moment C1 (per-batch):** two large cards, not a switch — **"Keep in the pool"** (default, padlock icon, *"visible to you and people in the photos"*) vs **"Share to the big screen"** (kiosk icon, *"eligible for the public gallery and kiosk after a dignity check"*) — plus a collapsed "keep just for me" (Ring 0). Placed on the send sheet itself, exactly where sharing intent forms; never a popup afterward. Send → the **upload filmstrip** (§6-adjacent, chips `uploading → curating → live 🎉 / 🔒 in the pool`).
5. **Me tab — two segments: My Album | My uploads.**
   - **Not yet enrolled:** the album segment shows the **"Unlock your album"** invitation → **consent moment C2 (biometric):** full-screen ritual — camera circle with animated gold ring, live-camera-only capture, full-sentence copy (*"…your face data stays inside this event and is deleted with it"*), explicit un-pre-ticked checkbox, delete-my-data link. If the claim-size gate holds the claim (spec 02 §3), the album shows its own uploads plus *"the host is confirming it's you"* — a designed pending state, not an error.
   - **Enrolled — My Album:** face-matched, taste-ranked grid. Header row: **"✨ Curate my album"** pill (→ swipe deck, below) and **"Save all"** (export: Web Share / zip / Photos-if-P1, spec 02 §6). Lightbox actions on any photo of you: share, save, and **"Hide me from public"** (eye-slash) = **consent moment C4 (subject veto)** — one tap, effective in seconds.
   - **The swipe deck (spec 07) — placement decided:** it lives *inside* My Album as an explicit mode, entered via the Curate pill (≤ 2 taps from anywhere) — plus a one-time suggestion chip once the album holds ≥ 12 unswiped photos (*"15 seconds to teach your album your taste"*). Full-screen card stack: swipe right / ❤ = love, left / 🙈 = hide, down = skip; visible buttons mirror the gestures (accessibility + discoverability); progress "8 of 20"; exit × anytime. End card: *"Your album just got smarter"* → deck dismisses to the **visibly reordered** album — the payoff moment, and a demo beat. It never interrupts any other flow.
   - **My uploads:** every upload with its **padlock chip** = **consent moment C3 (per-photo retroactive):** tap → 3-option sheet (big screen / pool / just me), effective in seconds via `recompute_visibility`. The 🎉 "your photo just went live on the kiosk" toast (spec 11 §3.4) fires here.
   - **Settings rows (bottom of Me):** 🔗 **Save your album link** (magic link — also surfaced once as a card after first upload; spec 02 §3), manage/revoke links, and **Delete my data** in a danger-zone row.
6. **Bounty banner** (spec 05 §3) overlays any tab (§6 choreography). Tapping **Shoot now** opens the camera with the `bountyId` attached to the batch (spec 01 §3) — the send sheet still shows C1 (bounty shots default to pool like everything else; the validator reads the photo regardless of ring).
7. **Leaderboard identity rule:** enrolled point-earners show name + avatar; un-enrolled uids render as **"Mystery guest 🎭"** with a self-only nudge (*"that's you — enroll to claim your name"*). Points are never lost; identity is only needed to *display* (spec 02 §1).

### 5.3 Kiosk operator flow

`/kiosk/{eventId}` → setup screen (event name, connection check, one instruction) → **"Start show"** tap = audio unlock + Screen Wake Lock (spec 04 §4) → fullscreen show. During hero slots a **small join-QR** sits bottom-left under the monogram (drives the loop: see the wall → scan → shoot); `bounty_call` slots enlarge it full-size. Wi-Fi drop → cached playlist loops with a quiet "reconnecting" glyph — never a blank screen.

### 5.4 Host journey

Create at `/host` → wizard (spec 08 §3: details+timezone → **template picker as full-bleed theme cards, selecting flips `data-theme` live — the demo beat** → dials → timeline paste+review → VIP enrollment/tiering + **family links** (spec 02 §3) → QR/links) → console. **The console is fully responsive — hosts operate from a phone at the venue**, and the two panic-critical controls are **persistent in the header across every tab**: the red **Freeze Public** shield (hold-to-confirm 600ms; ≤ 2s effect, spec 08 §5) and **Run director now**. Everything else lives in its spec 08 §4 tab.

### 5.5 The demo camera path (every screen in the unedited take, in order — each must be flawless)

| Beat (video PLAN §C) | Screen | Designed moment |
|---|---|---|
| QR scan → join | Welcome → Event tab | 2-tap entry, consent notice visible |
| Selfie consent | C2 ritual screen | the screenshot-worthy consent frame |
| Upload 10 | Send sheet (C1) → filmstrip | state chips ticking |
| Pipeline flows | Flight Deck (`?present=1`) | chips through lanes, cost ticker |
| Kiosk pops | Kiosk hero + just_in strip | "phone → wall in 2s" |
| Run director | Console header button → tick card | one tap, no tab hunting |
| Bounty lands | Phone 2 banner | the Twist, felt |
| Snap → points | Camera → award burst | confetti + count-up |
| Album fills | Me tab | face-match toast |
| Premiere | Kiosk takeover | Lyria audible |

No screen on this path may show a spinner, an empty state, or a dead end (§4 rule + §6/§7 states).

## 6. Kiosk — the show (visual contract per slot type, extends spec 04 §4)

Full-bleed, zero chrome, 3% safe margins. Exactly three permanent overlays:
- **Bottom-left:** event monogram (Fraunces) + active stage name in the stage accent + the small join-QR (§5.3).
- **Bottom-right — the live status glyph:** `● LIVE · 1,214 photos · directed by agents · updated 4s ago`. Contract: photo count = Firestore aggregate; "updated" = real last playlist-revision timestamp; the ● pulses only while the publisher lease is actually held. Truthful counters are the Flight Deck thesis in one line of UI.

Minimum sizes at 1080p viewed from ~5m: captions ≥ 40px, status glyph ≥ 24px, bounty poster headline ≥ 61px, join-QR ≥ 140px.

**Per-slot design:**
- **`hero`:** face-anchored Ken Burns (§4); Curator's caption bottom-center in Fraunces italic (the LLM's judgment displayed as product copy — e.g. *"The groom's cousins escalate the Haldi"*); small credit chip `📸 Ananya · +50` (uploader's display name if enrolled, else "a guest"; points shown when a bounty award produced the shot — the gamification loop made visible).
- **`just_in`:** 96px filmstrip along the bottom edge; new public items slide in from the right with a "just now" tag, ordered by upload time, recency-only — **this strip is the "your photo is on the wall" guarantee** (see spec 04 §4 and the demo-event `publicFloor` note in spec 09 §5: in the `protected_demo` event, consent + safety alone decide `public`, so a judge's test shot always reaches the wall within seconds; quality still owns the *hero* slots via the aesthetic term).
- **`reel` premiere takeover (storyboard):** screen dims to 40% over 600ms → gold title card, eyebrow `TONIGHT'S PREMIERE` (letterspaced caps, 20px) over the reel title in Fraunces 61px (*"The Couple"*) → 3s hold with shimmer → reel plays with Lyria audio (post the one-tap audio unlock, spec 04 §4) → end card: *"Directed by Showrunner · soundtrack composed by Lyria"*. This is the scheduled surprise that lands mid-unedited-take — autonomy made physically visible.
- **`bounty_call` — the wanted poster:** full-screen in the stage accent. Eyebrow `THE DIRECTOR NEEDS`, the ask in Fraunces (*"the bride's mother at the pheras"*), points badge `+150` in a gold pill, join-QR enlarged, countdown bar if the bounty is escalating. The Twist commanding the room, not just buzzing phones.
- **`leaderboard`:** top-5, end-credits styling (centered column, Fraunces names, tabular-num points), 8s hold.
- **`collage`:** displayed with a 1px gold hairline frame + stage title.

## 7. Guest PWA (component-level contracts; placements in §5.2)

**Nav model:** three-tab bottom bar — **Event** (public gallery + reels + leaderboard/missions entries) · **Camera** (center FAB, one tap to shutter) · **Me** (album, uploads, swipe deck, settings) — plus overlay surfaces (bounty banner, toasts). No hamburger, no settings maze, no chat.

**Bounty banner — the money interaction (design it as a mission briefing, not a notification):**
- **Anatomy:** slides down from top, `--radius-banner` glass card with gold hairline. Eyebrow `THE DIRECTOR ASKS` → the ask in Fraunces 20px → points pill `+150` → conic-gradient countdown ring (bounty expiry) → primary button **Shoot now** (opens camera directly, bountyId attached) + quiet dismiss.
- **Choreography:** entrance 320ms slide+fade on `--ease-luxe` + soft haptic (`navigator.vibrate([30,40,30])` where supported). On award: check-morph 180ms → points count-up 600ms (tabular nums) → themed confetti burst 1.2s (≤60 particles) + haptic. On expiry: quiet 240ms retreat, no shame state.
- A phone showing this is in frame during the unedited take — this animation **is** the Twist, felt.

**Upload filmstrip — the pipeline in product form:** horizontal strip on send; each thumb carries a state chip mapping 1:1 to real media state: `uploading` (shimmer) → `curating` (gold dot pulse) → `live 🎉` (visibility flipped `public`) or `🔒 in the pool` (`pool` ring). A judge who never opens the Flight Deck still *sees* the state machine.

**Offline honesty (venue Wi-Fi is hostile):** a slim top banner "📶 reconnecting — your uploads are safe" whenever the Firestore connection drops; the outbox (spec 01 §2) keeps queuing and the filmstrip keeps rendering from IndexedDB truthfully. Never a modal, never a blank screen.

**Empty states (copy deck — no blank screens anywhere):**
| Surface | Copy |
|---|---|
| Gallery, pre-first-photo | "The kiosk is waiting for its first photo. Scan, shoot, make history." |
| My album, pre-enrollment | "Take a selfie and every photo of you finds its way here." |
| My album, claim held (spec 02 §3 gate) | "The host is confirming it's you — your own shots are already here." |
| Bounties, none active | "The director is watching the coverage. Missions will appear here." |
| Reels row, none published | "Tonight's premieres are still in the edit room." |

## 8. Host console — the producer's booth

Editorial dashboard on `--bg-1` cards with gold hairlines; denser type (14/16px); responsive down to phone width (§5.4 — hosts run events from their pocket); this surface may scroll — the kiosk never does.

- **Persistent header (every tab):** event name + status pill, **Run director now**, and the red **Freeze Public** shield (hold-to-confirm 600ms — a visible kill switch is judge catnip; ≤2s effect per spec 08 §5).
- **KPI header row:** photos · guests · coverage % · cost-so-far (tabular nums, real aggregates only).
- **Coverage heat-grid** (the Story Director's ledger, spec 05, made visual): stages × required moments; cells amber → green as coverage fills; a red cell = the gap the next bounty will target. Screenshots directly into blog image #4.
- **Review queue:** Guardian gray-zone cards (three big actions: Approve public / Keep private / Block) **and claim-review cards** (spec 02 §3: enrollment selfie beside 4 cluster exemplars, Approve / Deny — designed as a five-second visual check).
- **People tab:** tier badges — 👑 tier 0 · ★ tier 1 · ● tier 2 · unmarked tier 3 — the audited **Feature this person** toggle (spec 11 §3.5) with its audit note inline ("featured by you, 21:14"), and the **activity feed** (claims, feature actions, panic actions — the audit trail as UI).
- **Controls tab:** the master switch (draft→live→paused→wrapping→wrapped) as a stepped physical control; host links + family links management; wrap-up report.
- **Creation wizard:** template picker as full-bleed theme cards — selecting one flips `data-theme` live (§3, the demo beat); sensitivity dials as labeled segmented controls; the tier-defaults explainer under topology (*"Bachelor party: everyone starts as inner circle"*).
- **"Why this photo?" overlay** (host/judge mode only; spec 04 §4): tap any kiosk slot or Highlight → glass card with the real stored score factors — `aesthetic 0.82 × vip 3.0 (Principal in frame) × freshness 1.4 × stage 1.0 → rank #1 · consent ✓ · Guardian ✓`. Zero new computation — it renders numbers the publisher already stored. Glass-box ranking; the Flight Deck thesis applied to the kiosk.

## 9. Flight Deck — mission control (deliberate contrast)

Separate token accent set on the same base: near-black `#070708`, JetBrains Mono numerals, no serif anywhere. Worker lanes color-coded (curate gold / face violet / safety crimson / render mint); chips flow left→right with 240ms eases; director agent cards pulse a soft ring while a tick is reasoning; a **Model Armor interception** renders as a red chip physically deflected out of the lane (the injection-block demo moment, spec 10 truthful-by-construction rules apply — every chip is a real pulse-shard event); cost ticker bottom-right in mono. The aesthetic contrast with the guest surfaces is itself a statement to say on camera: *two audiences, two visual languages, one system.*

## 10. Accessibility & performance budget

- WCAG AA contrast on every text/background pair (gold-on-maroon passes only at ≥20px/600 — body text is always `--ivory`; run an automated audit before Day-5 freeze).
- `prefers-reduced-motion` honored everywhere (§4); tap targets ≥ 44px; focus rings visible (gold, 2px) for keyboard/host use; swipe gestures always have visible button equivalents (§5.2).
- Guest PWA budget on mid-range Android over venue Wi-Fi: LCP < 2.5s, JS < 200KB gzipped per route (code-split per surface: /join, /kiosk, /host, /flightdeck load none of each other's chunks).
- Images: `thumb_384.webp` in grids, `display_1600` in lightbox (spec 04 §3); kiosk preloads the next 2 slots' assets so crossfades never stall.

## 11. Implementation notes

- Tailwind v4 with `@theme` bound to `tokens.css`; **no hex literals outside `tokens.css`** (grep check in review).
- Motion: Framer Motion for choreography (banner, premiere, confetti); plain CSS transitions for everything simpler.
- Fonts via `next/font/local` (Fraunces, Inter, JetBrains Mono — all OFL): no external font CDN (video/content rules: no third-party anything in frame; also removes a venue-Wi-Fi failure mode).
- Theme switching = `data-theme`/`data-stage` attributes only; compatible with the static export deployment (spec 09 §1) — zero SSR requirement.
- Confetti/particles: tiny in-house canvas helper (≤60 particles, themed colors), not a heavy dependency.
- All clients (including kiosk) perform silent anonymous auth on load — required for media-doc reads under spec 09 §3 rules.

## 12. Acceptance criteria

- [ ] Wizard template flip retunes every open surface (PWA, kiosk, console) live via `data-theme` — no reload, no re-render flash (the demo beat works on camera).
- [ ] Grep check: zero color hex literals outside `frontend/styles/tokens.css`.
- [ ] No indeterminate spinner exists on any P0 surface; every async state shows shimmer + agent-verb copy (§4 table); every surface has its §7 empty state (no blank screens).
- [ ] Screen-map placements hold: joining ≤ 2 taps from QR scan and uploading ≤ 2 more; per-batch consent (C1) is on the send sheet itself, never a post-upload modal; the padlock override (C3) is on every photo in My uploads; subject veto (C4) is in the album lightbox; the swipe deck is reachable in ≤ 2 taps from the Me tab and never interrupts another flow.
- [ ] Freeze Public and Run director now are reachable from **every** host console tab (persistent header) on both desktop and phone widths.
- [ ] Bounty banner full choreography (entrance → countdown → award burst) runs at 60fps on a mid-range Android; haptics fire where supported; `prefers-reduced-motion` collapses it to fades.
- [ ] Kiosk hero slots never crop a face (face-anchored Ken Burns verified against seeded fixtures with edge-positioned faces); the join-QR is decodable from 5 m during hero slots.
- [ ] Live status glyph numbers trace to real aggregates (code review: no literal or placeholder values); ● pulse is bound to actual lease possession.
- [ ] Upload filmstrip chips transition `uploading → curating → live/pool` driven by real media-doc listener events, matching each doc's actual state; the offline banner appears on connection drop and the outbox keeps draining truthfully.
- [ ] "Why this photo?" overlay renders only stored score components (no recomputation) and is gated to host/judge mode.
- [ ] Automated AA contrast audit passes on all four surfaces; tap targets ≥ 44px verified on the guest PWA.
- [ ] The muted-2× test: a viewer watching the 4-min video muted at 2× can follow every beat of §5.5's camera path from on-screen text and motion alone (rehearsal-day check, video/PLAN.md).
