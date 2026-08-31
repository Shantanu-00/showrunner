// Live Firestore listeners — spec 04 §3's "all are live queries, zero polling" and spec 03 §2's
// "no client ever polls". Every gallery/kiosk/album surface subscribes here; nothing in this
// file ever calls getDocs() on a poll interval.
//
// **Every listener here now requires event membership, and not one filter changed to get it.** That is
// the payoff of expressing membership as a custom claim (`members: [eventId, …]`, minted by
// `POST /v1/events/{eventId}/join`) rather than as a field on a document: `isMember(eventId)` in
// `firestore.rules` reads the token, so there is nothing for a query to filter on and nothing a client
// could get subtly wrong. Contrast the `visibility`/`status` pair, which *is* document state and
// therefore has to appear in every query below — a Firestore query whose filters do not guarantee the
// read rule fails entirely, so those filters and the rules are one design.
//
// The consequence for callers is a sequencing rule, not a query rule: `lib/membership.ts`'s
// `ensureMembership(eventId)` must have resolved before any subscribe below, because a listener opened
// against a token that predates the claim is denied and does not retry. Every guest shell awaits it.
// `rules-tests/run_matrix.py`'s `queries` group asserts each of these shapes across the boundary in
// both directions.

import {
  collection,
  deleteDoc,
  doc,
  getCountFromServer,
  getDoc,
  limit,
  onSnapshot,
  orderBy,
  query,
  serverTimestamp,
  setDoc,
  Timestamp,
  where,
  type Unsubscribe,
} from "firebase/firestore";
import { db, getUid } from "./firebase";
import type { BountyDoc, KioskPlaylist, LeaderboardEntry, MediaDoc, PersonDoc, ReelDoc } from "./types";

const GRID_LIMIT = 60;

function mediaCol(eventId: string) {
  return collection(db, "events", eventId, "media");
}

/** Public gallery (spec 04 §3): `visibility=='public' && status=='indexed'`, capturedAt desc. */
export function listenPublicGallery(
  eventId: string,
  onData: (items: MediaDoc[]) => void,
  onError: (err: Error) => void
): Unsubscribe {
  const q = query(
    mediaCol(eventId),
    where("visibility", "==", "public"),
    where("status", "==", "indexed"),
    orderBy("capturedAt", "desc"),
    limit(GRID_LIMIT)
  );
  return onSnapshot(
    q,
    (snap) => onData(snap.docs.map((d) => d.data() as MediaDoc)),
    onError
  );
}

/** Highlights tab (spec 04 §3): same gate + `isHighlight==true`, aesthetic-ordered; the
 * vipWeight re-rank on top of this is a client-side, deterministic re-sort (lib/scoring.ts) —
 * shared for every viewer, never personalized.
 *
 * `status=='indexed'` is not optional here even though spec 04 §3 only names it on the main gallery
 * query: spec 04 §2 requires it on *every* public-surface query, and the security rules enforce
 * exactly that pair. A photo can be `public` for a second or two before its last stage lands, and a
 * Firestore query whose filters don't guarantee the read rule fails outright rather than skipping the
 * document — so leaving it off would make Highlights break intermittently under load, which is the
 * worst possible way to discover it. */
export function listenHighlights(
  eventId: string,
  onData: (items: MediaDoc[]) => void,
  onError: (err: Error) => void
): Unsubscribe {
  const q = query(
    mediaCol(eventId),
    where("visibility", "==", "public"),
    where("status", "==", "indexed"),
    where("curator.isHighlight", "==", true),
    orderBy("curator.aestheticScore", "desc"),
    limit(GRID_LIMIT)
  );
  return onSnapshot(
    q,
    (snap) => onData(snap.docs.map((d) => d.data() as MediaDoc)),
    onError
  );
}

/** Private album (spec 04 §3): `albumOf array-contains personId`, capturedAt desc.
 *
 * The `visibility in ['pool','public']` filter is the rule boundary, not a nicety. A subject appears
 * in `albumOf` even on an item whose uploader chose Ring 0, and spec 04 §2 says a `self` item belongs
 * to its uploader alone — so the security rule denies those. Firestore fails a *whole query* when a
 * returned document is denied, which means the filter has to exclude them client-side or the album
 * breaks the first time anyone in it uploads a private photo. */
export function listenPrivateAlbum(
  eventId: string,
  personId: string,
  onData: (items: MediaDoc[]) => void,
  onError: (err: Error) => void
): Unsubscribe {
  const q = query(
    mediaCol(eventId),
    where("albumOf", "array-contains", personId),
    where("visibility", "in", ["pool", "public"]),
    orderBy("capturedAt", "desc"),
    limit(GRID_LIMIT)
  );
  return onSnapshot(
    q,
    (snap) => onData(snap.docs.map((d) => d.data() as MediaDoc)),
    onError
  );
}

/** My uploads (spec 04 §3): `uploaderUid == uid`, createdAt desc — every ring, it's their own. */
export function listenMyUploads(
  eventId: string,
  uid: string,
  onData: (items: MediaDoc[]) => void,
  onError: (err: Error) => void
): Unsubscribe {
  const q = query(
    mediaCol(eventId),
    where("uploaderUid", "==", uid),
    orderBy("createdAt", "desc"),
    limit(GRID_LIMIT)
  );
  return onSnapshot(
    q,
    (snap) => onData(snap.docs.map((d) => d.data() as MediaDoc)),
    onError
  );
}

/** Uploader display name for the hero credit chip (spec 12 §6): `guests/{uid}.personId` →
 * `people/{personId}.displayName`, falling back to "a guest" the moment either hop is missing —
 * never fabricated, just honestly absent until the uploader enrolls. */
export function listenUploaderCredit(
  eventId: string,
  uid: string,
  onName: (displayName: string | null) => void
): Unsubscribe {
  let innerUnsub: Unsubscribe | null = null;
  const outerUnsub = onSnapshot(
    doc(db, "events", eventId, "guests", uid),
    (guestSnap) => {
      innerUnsub?.();
      innerUnsub = null;
      const personId = guestSnap.exists() ? (guestSnap.data().personId as string | undefined) : undefined;
      if (!personId) {
        onName(null);
        return;
      }
      innerUnsub = onSnapshot(
        doc(db, "events", eventId, "people", personId),
        (personSnap) => onName(personSnap.exists() ? (personSnap.data().displayName as string | null) ?? null : null),
        () => onName(null)
      );
    },
    () => onName(null)
  );
  return () => {
    innerUnsub?.();
    outerUnsub();
  };
}

/** The `just_in` kiosk slot (spec 04 §4): "ordered by upload time, recency only — no score
 * term, no curation", so unlike hero/collage/bounty_call it needs no publisher-chosen mediaId —
 * the kiosk derives its own contents from the same `uploadedAt`-ordered index the publisher
 * would use. This is the "your photo is on the wall" guarantee. */
export function listenJustIn(
  eventId: string,
  liveWindowSec: number,
  onData: (items: MediaDoc[]) => void,
  onError: (err: Error) => void
): Unsubscribe {
  const since = Timestamp.fromMillis(Date.now() - liveWindowSec * 1000);
  const q = query(
    mediaCol(eventId),
    where("visibility", "==", "public"),
    where("status", "==", "indexed"),
    where("uploadedAt", ">=", since),
    orderBy("uploadedAt", "desc"),
    limit(20)
  );
  return onSnapshot(
    q,
    (snap) => onData(snap.docs.map((d) => d.data() as MediaDoc)),
    onError
  );
}

/** One media doc — used by the kiosk to resolve a slot's `mediaId` into renderable fields. */
export function listenMedia(
  eventId: string,
  mediaId: string,
  onData: (item: MediaDoc | null) => void,
  onError: (err: Error) => void
): Unsubscribe {
  return onSnapshot(
    doc(db, "events", eventId, "media", mediaId),
    (snap) => {
      const item = snap.exists() ? (snap.data() as MediaDoc) : null;
      // Every snapshot refreshes the warm cache below, so a slide the wall loops back around to is
      // seeded from the newest version of the document rather than the one the prefetch first read.
      if (item) mediaDocCache.set(mediaDocKey(eventId, mediaId), item);
      onData(item);
    },
    onError
  );
}

/* ------------------------------------------------------------------ the media-document warm cache
 *
 * A kiosk slide needs two independent things before it can paint: the photograph's bytes, and the
 * *document* that says which variant exists and where the faces are. `lib/kioskPrefetch.ts` warmed the
 * first and not the second, so a prefetched slide still opened on a shimmer while `listenMedia`'s
 * first snapshot travelled — a spinner caused by the one thing that was supposed to remove spinners.
 *
 * One `getDoc` per upcoming slide, cached by (event, media). `HeroSlot` seeds its initial state from
 * this synchronously, so a warmed slide's first render already has a variant and a face box; the
 * listener then takes over and keeps the entry current. */

const mediaDocCache = new Map<string, MediaDoc>();
const mediaDocInflight = new Map<string, Promise<MediaDoc | null>>();

function mediaDocKey(eventId: string, mediaId: string): string {
  return `${eventId}:${mediaId}`;
}

/** The synchronous read. Null when this document has not been fetched or listened to yet. */
export function cachedMediaDoc(eventId: string, mediaId: string | null | undefined): MediaDoc | null {
  if (!mediaId) return null;
  return mediaDocCache.get(mediaDocKey(eventId, mediaId)) ?? null;
}

/** Fetch and cache one media document. Resolves to it (or null if it is unreadable — a rules denial on
 * a photo that just lost eligibility is an ordinary outcome here, not an error worth surfacing). */
export async function prefetchMediaDoc(
  eventId: string,
  mediaId: string
): Promise<MediaDoc | null> {
  const key = mediaDocKey(eventId, mediaId);
  const warm = mediaDocCache.get(key);
  if (warm) return warm;
  const pending = mediaDocInflight.get(key);
  if (pending) return pending;

  const task = (async () => {
    try {
      const snap = await getDoc(doc(db, "events", eventId, "media", mediaId));
      if (!snap.exists()) return null;
      const item = snap.data() as MediaDoc;
      mediaDocCache.set(key, item);
      return item;
    } catch {
      return null;
    } finally {
      mediaDocInflight.delete(key);
    }
  })();
  mediaDocInflight.set(key, task);
  return task;
}

/** The kiosk's truthful photo count (spec 12 §6's live status glyph). A real Firestore
 * aggregate, re-fetched only when the playlist tells us something changed (revision bump) —
 * event-driven, not a timer poll. No realtime aggregate listener exists yet in this SDK. */
export async function countPublicIndexed(eventId: string): Promise<number> {
  const q = query(
    mediaCol(eventId),
    where("visibility", "==", "public"),
    where("status", "==", "indexed")
  );
  const snap = await getCountFromServer(q);
  return snap.data().count;
}

/** `events/{eventId}/kiosk/playlist` — the publisher's program (spec 04 §4). */
export function listenKioskPlaylist(
  eventId: string,
  onData: (playlist: KioskPlaylist | null) => void,
  onError: (err: Error) => void
): Unsubscribe {
  return onSnapshot(
    doc(db, "events", eventId, "kiosk", "playlist"),
    (snap) => onData(snap.exists() ? (snap.data() as KioskPlaylist) : null),
    onError
  );
}

/** Own person doc — display name, tier, enrollment state. */
export function listenPerson(
  eventId: string,
  personId: string,
  onData: (person: PersonDoc | null) => void,
  onError: (err: Error) => void
): Unsubscribe {
  return onSnapshot(
    doc(db, "events", eventId, "people", personId),
    (snap) => onData(snap.exists() ? (snap.data() as PersonDoc) : null),
    onError
  );
}

/** The event's full people roster — small collection, used only for the deterministic
 * tier→vipWeight lookup behind Highlights ranking and the "Why this photo?" card.
 *
 * This and `listenPeopleDirectory` below read *whole* person documents, which is why the person
 * document must hold nothing but what a wall already shows. `uidLinks` (the uid↔person map) and spec
 * 07 §2's `tasteProfile`/`tasteMemo` used to live here and now live in `people/{personId}/private/profile`,
 * deny-all to every client — a rule cannot grant `displayName` and withhold the rest of a document, so
 * the only way to keep them private was to move them. Nothing on this path changed as a result: neither
 * listener ever read those fields, and `PersonDoc` never declared them. */
export function listenPeopleTiers(
  eventId: string,
  onData: (tierByPersonId: Record<string, number>) => void,
  onError: (err: Error) => void
): Unsubscribe {
  return onSnapshot(
    collection(db, "events", eventId, "people"),
    (snap) => {
      const map: Record<string, number> = {};
      snap.docs.forEach((d) => {
        const p = d.data() as PersonDoc;
        map[p.personId ?? d.id] = p.tier ?? 3;
      });
      onData(map);
    },
    onError
  );
}

/** The event's people roster, whole documents — the host console's People panel (spec 13 §7).
 * Distinct from `listenPeopleTiers`/`listenPeopleDirectory` above, which narrow to exactly the
 * one field they need; this is the one place `hostEnrolled`/`claimApproved` are read, and both
 * are host-console-only fields no guest surface consumes. Ordered by `createdAt` so a host who
 * just added someone sees them land at a stable position rather than reshuffling on every
 * unrelated Firestore write. */
export function listenPeople(
  eventId: string,
  onData: (people: PersonDoc[]) => void,
  onError: (err: Error) => void
): Unsubscribe {
  const q = query(collection(db, "events", eventId, "people"), orderBy("createdAt", "asc"));
  return onSnapshot(
    q,
    (snap) =>
      onData(
        snap.docs.map((d) => {
          const p = d.data() as PersonDoc;
          return { ...p, personId: p.personId ?? d.id };
        })
      ),
    onError
  );
}

/** Display-name directory for the leaderboard slot (spec 12 §5.2 point 7): un-enrolled uids
 * render as "Mystery guest 🎭" — never blocked, points are never lost, only the name is missing. */
export function listenPeopleDirectory(
  eventId: string,
  onData: (nameByPersonId: Record<string, string | null>) => void,
  onError: (err: Error) => void
): Unsubscribe {
  return onSnapshot(
    collection(db, "events", eventId, "people"),
    (snap) => {
      const map: Record<string, string | null> = {};
      snap.docs.forEach((d) => {
        const p = d.data() as PersonDoc;
        map[p.personId ?? d.id] = p.displayName ?? null;
      });
      onData(map);
    },
    onError
  );
}

/** Every open bounty (spec 05 §3's `active`/`escalated`) — the missions sheet and the guest
 * banner (spec 12 §7) both read from this one listener rather than each running their own query. */
/** Firestore hands back a `Timestamp` for every timestamp field, never a string — so the interfaces
 * in `lib/types.ts` that declare `createdAt?: string | null` are describing the *backend's* JSON
 * shape, not what a listener sees. A blind `d.data() as SomeDoc` cast hides that completely, and the
 * two ways it goes wrong are both real: `.localeCompare` on a Timestamp **throws** (it took the whole
 * `/join` page down as soon as an event had one active bounty), and `new Date(timestamp).getTime()`
 * returns `NaN`, which is falsy, so a countdown silently sits full forever instead of counting.
 *
 * Normalising to epoch millis at this boundary is the fix: a number is unambiguous, comparable and
 * cheap, and every consumer stops having to guess. Accepts the other shapes too, because seeded
 * emulator fixtures carry ISO strings. */
export function tsMillis(value: unknown): number | null {
  if (value == null) return null;
  if (value instanceof Timestamp) return value.toMillis();
  if (value instanceof Date) return value.getTime();
  if (typeof value === "number") return value;
  if (typeof value === "string") {
    const ms = Date.parse(value);
    return Number.isNaN(ms) ? null : ms;
  }
  return null;
}

export function listenActiveBounties(
  eventId: string,
  onData: (items: BountyDoc[]) => void,
  onError: (err: Error) => void
): Unsubscribe {
  const q = query(
    collection(db, "events", eventId, "bounties"),
    where("status", "in", ["active", "escalated"])
  );
  return onSnapshot(
    q,
    (snap) => {
      const uid = getUid();
      onData(
        snap.docs
          .map((d) => {
            const raw = d.data();
            return {
              ...(raw as BountyDoc),
              createdAtMs: tsMillis(raw.createdAt),
              expiresAtMs: tsMillis(raw.expiresAt),
            };
          })
          // Display-courtesy filter (spec 13 §6), applied here so the banner, the missions
          // sheet and the mission-count badge all agree: an `assignee` bounty shows only to
          // the uid it names. The server flips it to a broadcast on its own if unanswered —
          // delivery routing only, never who gets paid.
          .filter((b) => b.audience !== "assignee" || !b.assigneeUid || b.assigneeUid === uid)
      );
    },
    onError
  );
}

/** `guests/{uid}.points` — the one signal the award burst needs (spec 12 §7): any *increase*
 * is a fulfilled bounty (or any future point source) without the client having to reconstruct
 * which submission earned it, since `points` is already the ledger's own running total. */
export function listenGuestPoints(
  eventId: string,
  uid: string,
  onPoints: (points: number) => void
): Unsubscribe {
  return onSnapshot(
    doc(db, "events", eventId, "guests", uid),
    (snap) => onPoints(snap.exists() ? (snap.data().points as number | undefined) ?? 0 : 0),
    () => onPoints(0)
  );
}

/** `bounty_call` slot (spec 04 §4 references it by `bountyId`; spec 05 owns the doc). */
export function listenBounty(
  eventId: string,
  bountyId: string,
  onData: (bounty: BountyDoc | null) => void,
  onError: (err: Error) => void
): Unsubscribe {
  return onSnapshot(
    doc(db, "events", eventId, "bounties", bountyId),
    (snap) => onData(snap.exists() ? (snap.data() as BountyDoc) : null),
    onError
  );
}

/** `reel` premiere slot (spec 06 owns the doc). */
export function listenReel(
  eventId: string,
  reelId: string,
  onData: (reel: ReelDoc | null) => void,
  onError: (err: Error) => void
): Unsubscribe {
  return onSnapshot(
    doc(db, "events", eventId, "reels", reelId),
    (snap) => {
      const reel = snap.exists() ? (snap.data() as ReelDoc) : null;
      // Keep the warm copy current, so a premiere the wall loops back to opens on the newest status
      // (`rendering` → `published`) rather than the one the prefetch happened to read.
      if (reel) reelDocCache.set(`${eventId}:${reelId}`, reel);
      onData(reel);
    },
    onError
  );
}

/** The reel equivalent of `cachedMediaDoc`. A premiere's `videoUri` lives on this document, so the
 * kiosk cannot even begin fetching video until it arrives — which is why `lib/kioskPrefetch.ts` warms
 * it a slide early and `ReelSlot` seeds its state from here. */
const reelDocCache = new Map<string, ReelDoc>();
const reelDocInflight = new Map<string, Promise<ReelDoc | null>>();

export function cachedReelDoc(eventId: string, reelId: string | null | undefined): ReelDoc | null {
  if (!reelId) return null;
  return reelDocCache.get(`${eventId}:${reelId}`) ?? null;
}

export async function prefetchReelDoc(eventId: string, reelId: string): Promise<ReelDoc | null> {
  const key = `${eventId}:${reelId}`;
  const warm = reelDocCache.get(key);
  if (warm) return warm;
  const pending = reelDocInflight.get(key);
  if (pending) return pending;

  const task = (async () => {
    try {
      const snap = await getDoc(doc(db, "events", eventId, "reels", reelId));
      if (!snap.exists()) return null;
      const reel = snap.data() as ReelDoc;
      reelDocCache.set(key, reel);
      return reel;
    } catch {
      return null;
    } finally {
      reelDocInflight.delete(key);
    }
  })();
  reelDocInflight.set(key, task);
  return task;
}

/** The event's recap film, for the guests it is actually about (`RecapCard`).
 *
 * Two equality filters and **no `orderBy`**, deliberately: Firestore serves multiple equality
 * filters by merging single-field indexes, so this query needs no composite index added to
 * `firestore.indexes.json` — whereas `where(...).where(...).orderBy('version')` would. The newest
 * cut is picked in JS from at most a handful of documents instead.
 *
 * `visibility == 'public'` is not an optimisation, it is required. `firestore.rules` only grants a
 * member `reels/{id}` when that document is public, and Firestore fails an entire query if one
 * returned document is denied rather than skipping it — so dropping this filter does not widen the
 * result, it breaks the listener outright. Same lesson HANDOFF §4.17(a) records for Highlights and
 * the private album: the query filters and the security rules are one design.
 */
export function listenRecapReel(
  eventId: string,
  onData: (reel: (ReelDoc & { version?: number }) | null) => void,
  onError: (err: Error) => void
): Unsubscribe {
  return onSnapshot(
    query(
      collection(db, "events", eventId, "reels"),
      where("persona", "==", "event_recap"),
      where("visibility", "==", "public"),
      limit(5)
    ),
    (snap) => {
      const cuts = snap.docs.map((d) => d.data() as ReelDoc & { version?: number });
      if (cuts.length === 0) {
        onData(null);
        return;
      }
      // Highest `version` wins — spec 06 §4's supersession means a better cut is a new document, and
      // a retracted one has already dropped out of this query by losing `visibility == 'public'`.
      onData(cuts.reduce((best, c) => ((c.version ?? 1) > (best.version ?? 1) ? c : best)));
    },
    onError
  );
}

/** `people/{personId}/reactions` (spec 07 §1) — the one client write in the whole system
 * (`firestore.rules:159`). A heart on the private-album grid is this build's cheap path onto the
 * full swipe deck spec 07 §1 describes: same document shape (`{verdict, at}`), so nothing here
 * would need to change if the card-stack UI is ever built. Loved/hidden captions feed the
 * deterministic tag-affinity vector and, every 15 new reactions, a Gemma taste memo (spec 07 §2) —
 * both computed server-side by `directors/story/taste.py` from this collection. */
export type Reaction = "love" | "hide";

export function listenReactions(
  eventId: string,
  personId: string,
  onData: (verdictByMediaId: Record<string, Reaction>) => void
): Unsubscribe {
  return onSnapshot(
    collection(db, "events", eventId, "people", personId, "reactions"),
    (snap) => {
      const map: Record<string, Reaction> = {};
      snap.docs.forEach((d) => {
        const verdict = d.data().verdict as Reaction | undefined;
        if (verdict) map[d.id] = verdict;
      });
      onData(map);
    },
    () => onData({})
  );
}

/** Set or clear a reaction. `verdict: null` deletes the document — un-loving a photo is the same
 * right as loving it (`firestore.rules:164`'s own comment), and a delete carries no
 * `request.resource` so the rule needed its own branch rather than riding the shape check. */
export async function setReaction(
  eventId: string,
  personId: string,
  mediaId: string,
  verdict: Reaction | null
): Promise<void> {
  const ref = doc(db, "events", eventId, "people", personId, "reactions", mediaId);
  if (verdict === null) {
    await deleteDoc(ref);
    return;
  }
  await setDoc(ref, { verdict, at: serverTimestamp() });
}

/** Leaderboard slot (spec 04 §3 tree's `guests/{uid}`, spec 12 §6): top-N by points. */
export function listenLeaderboard(
  eventId: string,
  topN: number,
  onData: (entries: LeaderboardEntry[]) => void,
  onError: (err: Error) => void
): Unsubscribe {
  const q = query(collection(db, "events", eventId, "guests"), orderBy("points", "desc"), limit(topN));
  return onSnapshot(
    q,
    (snap) =>
      onData(
        snap.docs.map((d) => ({
          uid: d.id,
          personId: d.data().personId ?? null,
          points: d.data().points ?? 0,
        }))
      ),
    onError
  );
}
