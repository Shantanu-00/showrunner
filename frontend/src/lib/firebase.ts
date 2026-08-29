import { initializeApp, getApps, type FirebaseApp } from "firebase/app";
import {
  getAuth,
  connectAuthEmulator,
  signInAnonymously,
  onAuthStateChanged,
  type Auth,
  type User,
} from "firebase/auth";
import { getFirestore, connectFirestoreEmulator, type Firestore } from "firebase/firestore";

const firebaseConfig = {
  apiKey: process.env.NEXT_PUBLIC_FIREBASE_API_KEY,
  authDomain: process.env.NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN,
  projectId: process.env.NEXT_PUBLIC_FIREBASE_PROJECT_ID,
  appId: process.env.NEXT_PUBLIC_FIREBASE_APP_ID,
  messagingSenderId: process.env.NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID,
};

function getFirebaseApp(): FirebaseApp {
  return getApps()[0] ?? initializeApp(firebaseConfig);
}

let auth: Auth;
let db: Firestore;
let emulatorsConnected = false;

if (typeof window !== "undefined") {
  const app = getFirebaseApp();
  auth = getAuth(app);
  db = getFirestore(app);

  if (process.env.NEXT_PUBLIC_USE_EMULATOR === "1" && !emulatorsConnected) {
    connectAuthEmulator(auth, "http://localhost:9099");
    connectFirestoreEmulator(db, "localhost", 8080);
    emulatorsConnected = true;
  }
}

export { auth, db };

/** Silently signs in anonymously if no session exists yet. Idempotent. */
export function ensureAnonymousAuth(): Promise<User> {
  return new Promise((resolve, reject) => {
    const unsubscribe = onAuthStateChanged(
      auth,
      (user) => {
        unsubscribe();
        if (user) {
          resolve(user);
          return;
        }
        signInAnonymously(auth).then((cred) => resolve(cred.user), reject);
      },
      reject
    );
  });
}

export function getUid(): string | null {
  return auth?.currentUser?.uid ?? null;
}

/** The claims `firestore.rules` reads, as the client sees them.
 *
 * `hosts` and `members` are **arrays**, and both used to be — or would have been — a single string.
 * A scalar `host` claim silently revoked a host's first console the moment they created a second
 * event, because `set_custom_user_claims` overwrites rather than appends (`backend/shared/auth.py`);
 * membership has the same shape for the same reason, since one phone attends more than one event.
 * `host` is still surfaced because tokens minted before the change last an hour. */
export interface Claims {
  personId?: string;
  /** The legacy scalar, still honoured by `isHost()` in the rules. Prefer `hosts`. */
  host?: string;
  hosts: string[];
  members: string[];
}

function claimArray(raw: unknown, legacy?: unknown): string[] {
  const out = Array.isArray(raw) ? raw.filter((v): v is string => typeof v === "string") : [];
  if (typeof legacy === "string" && legacy && !out.includes(legacy)) out.push(legacy);
  return out;
}

/** Spec 02 §1: after enroll/claim/reclaim/join the server mints custom claims for the uid; the
 * client must force-refresh its ID token to see them. A Firestore listener started before this
 * resolves is evaluated against the *old* token and fails permission-denied — which is why
 * `lib/membership.ts` awaits this before any surface subscribes. */
export async function refreshClaims(): Promise<Claims> {
  const user = auth?.currentUser;
  if (!user) return { hosts: [], members: [] };
  const result = await user.getIdTokenResult(true);
  const legacyHost = typeof result.claims.host === "string" ? result.claims.host : undefined;
  return {
    personId: typeof result.claims.personId === "string" ? result.claims.personId : undefined,
    host: legacyHost,
    hosts: claimArray(result.claims.hosts, legacyHost),
    members: claimArray(result.claims.members),
  };
}

/** Whether this token already grants membership of `eventId` — a host claim counts, exactly as
 * `isMember(eventId)` in the rules ORs `isHost(eventId)` in. Read without a force-refresh, so it is
 * cheap enough to call before deciding whether a join round trip is needed at all. */
export async function hasMembership(eventId: string): Promise<boolean> {
  const user = auth?.currentUser;
  if (!user) return false;
  const result = await user.getIdTokenResult();
  return (
    claimArray(result.claims.members).includes(eventId) ||
    claimArray(result.claims.hosts, result.claims.host).includes(eventId)
  );
}
