# Spec 13 — Generic events & the multi-day timeline

Extends specs 03/04/05/06/08/11; consumed by all of them. This is the contract for the pivot from
template-led creation to **itinerary-led creation**, and for events whose timeline spans days (the
canonical instance: a 5-day group trip). Nothing here changes the trust architecture: visibility,
consent, biometrics and the money path keep exactly the writers and gates specs 02/04/05 give them.

The design principle throughout: **the timeline is a prior, the photos are evidence, and the host
is the referee.** The schedule anticipates; uploads reconcile; an override always wins.

## 1. Event dates & day semantics

- `Event.startsOn` / `Event.endsOn`: ISO **local dates** ("2026-10-12") in `Event.timezone` —
  never UTC instants, because "Day N" is a wall-clock concept and a UTC midnight lands on the
  wrong day for half the planet. Both-or-neither; `endsOn ≥ startsOn`.
- **Day indices are always derived, never stored** (`backend/shared/eventtime.py`; frontend mirror
  `frontend/src/lib/eventTime.ts`). A host correcting the start date mid-event
  (`POST …/details`) must leave no stale day number anywhere — and doesn't, because there is
  nowhere for one to live.
- `Event.expectedParticipants`: how many *people* the host expects. Distinct from `guestCount`
  (sessions; one human = several uids, spec 02 §1). Feeds §5's group threshold and the invite
  seat-cap default (`clamp(expectedParticipants × 3, 10, INVITE_DEFAULT_SEATS)` — ×3 because
  seats count sessions). `None` disables group-coverage logic entirely.
- **Degradation is the contract:** an undated event (`startsOn` absent — every pre-spec-13 event)
  gets `day_index() == None` and every surface renders exactly its pre-spec-13 time-only form. A
  malformed date or unknown timezone degrades the same way rather than failing anything —
  calendar labels are presentation, and presentation is never the reason a bounty wasn't issued.
- `EventStage` is unchanged: `startsAt`/`endsAt` were always absolute UTC datetimes and are
  multi-day capable by construction. `PUT /stages` (the only writer) sorts the array
  chronologically (undated stages last, stable), so array order and time order are one thing
  downstream.

## 2. Creation is itinerary-led, not template-led

`POST /v1/events` takes `{name, timezone, startDate?, endDate?, expectedParticipants?,
accessMode?}`. `templateId` defaults to `custom` (neutral dials, empty glossary) and the wizard
never sends it. `accessMode: "invite"` mints the join code inside the creation request — the
response carries it exactly once (`joinCode`), same plaintext-never-stored contract as
`AccessResponse.joinCode` — so an invite-only trip is never open for the minutes between the
wizard and the access panel.

The spec-11 Event Type Profiles survive **as data, never as an entry surface**: presets behind a
quiet `<select>` in the console's Settings panel (`POST …/profile`), prefilled client-side, always
host-reviewed. No card grid, no religious default, anywhere. Spec 11 §2's principle is unchanged
and strengthened: the system never infers cultural context; now it doesn't even *ask first*.

`POST …/details` (host, until wrapped): correct `name`, the date range (pairwise), or
`expectedParticipants`. A trip that slips a day is a real edit, same posture as spec 08 §6's
mid-event stage edits.

## 3. The itinerary parse: three modalities, dated proposals, review is still the law

`POST …/itinerary/parse` accepts any of: pasted text (≤8,000 chars), a PDF (≤10 MB), or an
image/screenshot (≤8 MB) — the file as base64 in the JSON body (`fileBase64` + `fileMime`, closed
mime list), the same shape `EnrollRequest.selfie` established. One endpoint, one content type;
the pre-spec-13 client keeps working.

- **Dated proposals:** the prompt now carries the event's own date range + timezone, and
  `ParsedStage` gains `proposedStartLocal`/`proposedEndLocal` ("YYYY-MM-DDTHH:MM", event-local) —
  emitted **only when the source states or plainly implies the day** ("Day 3", "Oct 14",
  "Tuesday", or a single-day source). Ambiguous → empty + `timeHint`, exactly the old behavior.
  These are **prefills for the host's review picker, never saved values**: `PUT /stages` remains
  the only writer of real UTC windows, so spec 08 §3.2's "never silently authoritative" survives
  intact — the pickers arrive filled instead of blank, and the host still confirms every one.
  The endpoint drops (blanks) any proposal that is unparseable or outside the range.
- **Model Armor, both halves:** the paste is guarded before the model sees it (unchanged). File
  bytes cannot be pre-guarded (Armor is text-only), so `ItineraryParseOut.sourceText` — the
  schedule text the model transcribed out of the file — is guarded *after* the parse, plus the
  extracted labels as belt-and-braces; a block rejects the whole proposal. And `PUT /stages`
  itself now guards stage + moment labels: they ride into per-photo prompts
  (`workers/curate/agent.py`), and the one writer is the one choke point that covers every input
  modality at once.

## 4. Multi-day runtime

- **One active-stage resolver** (`backend/shared/stages.py::resolve_active`):
  `stageOverride || activeStage || scheduled_now`, adopted by the ledger, the publisher, the
  public endpoint and both perception workers. The schedule leg is what puts "Now" on the wall of
  an event whose host never pressed the button and whose director hasn't advanced yet. The
  returned `source` is load-bearing: `override` means no auto-advance may fire.
- **Day-aware prompts:** the director's ledger renders event-local, day-indexed stamps
  ("Day 2 Tue 14:00–16:00", header "(day 3 of 5)"), lapsed stages are marked `ENDED`, and the
  session window's tick lines carry the day — the model's own memory is the one place it can
  learn a day has passed.
- **Gap lifecycle:** past `endsAt + STAGE_GAP_GRACE_MINUTES` (90; flagged, not spec-05-pinned) a
  stage emits no live gaps; its uncovered moments archive **exactly once** into
  `directorState.permanentGaps` (`archivedStages` is the exactly-once memory), where the wrap
  report reads them. Inside the grace window the gap is still live — "we're still at the
  restaurant" photos count.
- **Idle ticks:** nothing scheduled within `TICK_IDLE_LOOKAHEAD_MINUTES` (120) ahead or the grace
  window behind, zero velocity, no open bounty, no override → the tick runs Validate/Expire only
  (awards never wait), skips the REASON call, reports `mode:"idle"`, writes no session line
  (overnight NO_OPs must not push the evening the director worked out of its own window). Any
  condition the predicate can't be sure of keeps the director awake: unscheduled stage, no stages,
  a bounty that just expired this tick.
- **Evidence-driven advance:** see spec 05 §1/§2 (revised). Window =
  `max(45, 0.25 × minutes-to-nearest-neighbour-stage)`; OR sustained drift — the same target for
  `DRIFT_ADVANCE_TICKS` (2) consecutive ticks at confidence ≥ 0.8. The photos are the ground
  truth the schedule only anticipates; the override beats both.
- **Kiosk/gallery:** `previous` stage is by `startsAt`, not array position. The public payload
  gains `startsOn`/`endsOn` and a derived per-stage `day` — **day granularity only**, honouring
  that endpoint's no-stage-timing discipline. Gallery chips group by day ("Day 2 · Dinner");
  the kiosk header reads "Day N — {stage}". Kiosk stage theming keys off the generic
  `EventStage.theme` palette names (`gold|violet|crimson|ocean|forest|neon|slate|sunrise`),
  never off template-specific stage ids.

## 5. Group coverage & the expected-participants threshold

- The coverage shard (spec 05 §1's materialized view) gains an **increment-only people-count
  histogram** `peopleBuckets: {p1, p2_3, p4_6, p7_12, p13up}`, bumped from
  `curator.peopleCountEstimate` inside the same indexing transaction as every other counter.
  A histogram rather than a stored `groupShotCount`, because the threshold depends on
  `expectedParticipants`, which the host can edit — thresholds apply at read, and a max cannot be
  maintained write-only.
- New gap kind `"group"`, computed **per day**: if no shard belonging to today's stages has any
  bucket at ≥ `ceil(expectedParticipants × GROUP_SHOT_MIN_FRACTION)` (0.75; flagged) people, emit
  one gap — "no photo of the whole group today" — with severity ramping over the day's last
  scheduled stage (fixable-now urgency). Skipped entirely when `expectedParticipants` is unset.
  The prompt line names the target count so the copy can be honest ("get all 4 of you in one
  frame at dinner").
- **Validation floor:** a group bounty's fulfilment requires `peopleCountEstimate ≥ threshold` —
  a deterministic check in `validate.py` beside the existing ones, before the text-only moment
  question. The model still never decides who gets paid.

## 6. Targeted capture tasks (assignment is delivery, never pay)

- `Bounty` gains `assigneeUid`, `assignedAt`, `assignmentTimeoutAt`;
  `BountyAudience.ASSIGNEE`. **The model never picks the person**: when a decision carries
  `audience: assignee` (and by default for group-gap bounties), `act.py` resolves the assignee
  deterministically — the most recently active member (`guests/{uid}.lastSeenAt` within the
  nearStage window, ≥1 upload). The tick's deterministic Expire step clears an unanswered
  assignment after `BOUNTY_ASSIGN_TIMEOUT_MINUTES` (6 ≈ 3 demo ticks; flagged) and flips
  `audience → all` — broadcast on timeout, no model involved.
- Delivery is a client-side courtesy: an assigned bounty banners only for `assigneeUid == uid`
  until the timeout, then for everyone. Not security, and it doesn't need to be, because the
  **invariant** is: *assignment never changes who gets paid.* `validate.py::settle` is untouched
  by targeting — whoever submits the fulfilling photo gets the points, exactly as before. A
  bystander who happens to grab the group shot first is a success, not a leak.

## 7. Host participant enrollment (the People step)

`POST …/people/host-enroll` (host-authed): display name, tier, reference photo → embed →
person doc (`hostEnrolled: True`, `claimApproved: True` — the host *is* the approver) + embedding
in the deny-all `enrollments/{personId}`. This is `backend/seed.py::seed_person` promoted to a
product path, with the gates the seed never needed: the ban check and the hourly claim-attempt
cap, and a required "I have their permission to add their photo" acknowledgment (the reference
photo is host-asserted consent; the subject keeps subject-veto and delete-my-data, spec 02 §4/§5).

**§4.28's rule survives with no exceptions: no uid link is created here.** The person's *album*
accretes (that is the point — the director can see who's been photographed), but when the real
human self-enrolls, their selfie matches a host-enrolled person, the impersonation guard holds
the claim (`holdReason=protected_person`), and host approval remains the only grant of the
`personId` custom claim. Plus spec 11 §6's `POST …/people/{personId}/tier` (host-authed,
audited), finally implemented.

## 8. The wrap recap (event_recap) & the Event Diary

- New reel persona `event_recap`: SELECT allocates the candidate cap across **all** stages,
  round-robin weighted by each shard's `highlightCount`, reusing `stage_recap`'s per-stage
  filtering; every other pipeline step (DIRECT/CRITIC/EDL/Lyria/RENDER/publish) is unchanged.
  One-clip v1 (~30 s). Commissioned **deterministically** when the tick sees a `wrapping` event —
  the same "anticipate the predictable" posture as arming — through `commission.py`'s existing
  guardrails; the host can also re-commission via the existing `POST …/reels`. Download rides the
  existing visibility-rechecking 302 with `?download=1` → content-disposition attachment; the
  consent interlock applies automatically because the recap is just a reel.
- `WrapReport`: per-stage rows gain `dayLabel` and `bestMediaIds` (top-3 by stored
  `aestheticScore`, `visibility ∈ {pool, public}`); the report gains `recapReelId`; and
  `directorState.permanentGaps` finally merges into `honestGaps` — "we asked and never got it"
  reaches the host, as spec 05 §3 always promised.
- **Event Diary:** when a stage lapses, one flash-lite call distills its Curator outputs
  (captions, momentTags, scene counts, people mix) into a short qualitative memo → Memory Bank
  `{eventId}:diary:{stageId}` with a Firestore mirror `ledger/diary/{stageId}` (the
  `hostPreferences` fallback pattern). **Consumers: creative surfaces only** — the recap's
  narrative brief, the wrap headline, the director's bounty-copy context. **Binding boundary
  (HANDOFF §4.18): diary text never feeds ranking, visibility or points.** Off the critical
  path: a failed diary write is logged and skipped, never blocks a tick.

## 9. Flagged constants (chosen this build, none spec-05/09-pinned)

`STAGE_GAP_GRACE_MINUTES=90`, `TICK_IDLE_LOOKAHEAD_MINUTES=120`, `DRIFT_ADVANCE_TICKS=2`,
`GROUP_SHOT_MIN_FRACTION=0.75`, `BOUNTY_ASSIGN_TIMEOUT_MINUTES=6`, invite seat derivation
`×3 clamp [10, INVITE_DEFAULT_SEATS]`. All in `backend/shared/settings.py` beside the pinned
values they sit among, each with the flag comment.

## 10. Acceptance criteria

- [ ] A 5-day event parses from paste, PDF and screenshot to the same trip shape; proposals land
      inside the date range; an injection paste and an injection stage label are both deflected
      (`scripts/smoke_itinerary.py`).
- [ ] Day math, resolver precedence, gap lifecycle (grace + exactly-once archive), idle predicate,
      previous-by-time, evidence advance — all as offline truth tables; live: a lapsed Day-1
      moment arms nothing while the running stage arms, and a quiet event ticks `mode:"idle"`
      with zero tokens (`scripts/smoke_multiday.py`).
- [ ] Undated pre-spec-13 events behave byte-for-byte as before on every surface.
- [ ] Group gap appears only when `expectedParticipants` is set and no qualifying frame exists
      that day; a 4-person frame clears it; the fulfilment floor is deterministic
      (`scripts/smoke_group.py`).
- [ ] An assigned bounty banners only for the assignee until timeout, then broadcasts; awards are
      identical with and without assignment.
- [ ] Wrapping a multi-day event autonomously commissions one `event_recap`; the wrap report
      carries day labels, best-moment ids, the recap id and the permanent-gap record; diary memos
      exist per lapsed stage and a simulated diary failure leaves the tick green.
