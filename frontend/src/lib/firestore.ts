// Live Firestore listeners — spec 04 §3's "all are live queries, zero polling" and spec 03 §2's
// "no client ever polls". Every gallery/kiosk/album surface subscribes here; nothing in this
// file ever calls getDocs() on a poll interval.

import {
  collection,
  doc,
  getCountFromServer,
  limit,
  onSnapshot,
  orderBy,
  query,
  Timestamp,
  where,
  type Unsubscribe,
} from "firebase/firestore";
import { db } from "./firebase";
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
    (snap) => onData(snap.exists() ? (snap.data() as MediaDoc) : null),
    onError
  );
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
 * tier→vipWeight lookup behind Highlights ranking and the "Why this photo?" card. */
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
    (snap) => onData(snap.exists() ? (snap.data() as ReelDoc) : null),
    onError
  );
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
