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

/** Spec 02 §1: after enroll/claim/reclaim the server mints custom claims for the uid; the
 * client must force-refresh its ID token to see them. Returns the claims the rules also read. */
export async function refreshClaims(): Promise<{ personId?: string; host?: string }> {
  const user = auth?.currentUser;
  if (!user) return {};
  const result = await user.getIdTokenResult(true);
  return {
    personId: typeof result.claims.personId === "string" ? result.claims.personId : undefined,
    host: typeof result.claims.host === "string" ? result.claims.host : undefined,
  };
}
