# Firestore rules matrix

```bash
make rules-test        # firebase emulators:exec --only firestore "python rules-tests/run_matrix.py"
```

63 assertions, 8 groups, no network calls to Google — everything runs against the local Firestore
emulator using the rules file in the repository root.

## Why this exists

`firestore.rules` is the enforcement half of the project's core trust claim: **judgment by agents,
enforcement by policy.** Agents write opinions (an aesthetic score, a dignity verdict, a face match);
one deterministic transactional function, `recompute_visibility`, turns those opinions plus the
uploader's consent ring into a single `visibility` field; the rules serve strictly what that field
already decided. A claim like that is only worth making if crossing the boundary is *tried*, so each
row here is a named persona attempting a specific read or write, with the answer the architecture
requires and one line saying why.

The personas are the stranger / pool member / subject / uploader / host matrix the specs call for,
plus a platform admin and an unauthenticated client.

## What the interesting rows prove

| Row | The property |
|---|---|
| `stranger read media/public_processing` → deny | `visibility=='public'` alone is never enough. A photo can turn public a second before its last stage lands; the `status=='indexed'` term is what keeps a half-processed item off the wall. |
| `subject read media/self_ring0` → deny | Being *in* a photo does not override the uploader's Ring 0 choice. |
| `subject read media/blocked` → deny | A SafeSearch-blocked item is forced to the uploader alone — not even the people in it. |
| `host write media/…` → deny | Not even the host writes `visibility`. One writer, in one transaction, or the panic-freeze button is bypassable. |
| `host read enrollments/…` → deny | Face templates are unreachable from every client. Rules cannot hide a field, so the biometric lives in its own collection rather than on the person document that the kiosk credit chip and leaderboard need to read. |
| `subject write reactions {+points}` → deny | The one permitted client write is shape-checked: closed key set, closed verdict vocabulary. |
| `other_host read media/…` → deny | The `host` claim names one event. |
| `banned read media/public_indexed` → **allow** | Deliberate: a ban stops uploading (enforced in the API), not looking at a public wall. Stated rather than left ambiguous. |
| `… query album without visibility filter` → deny | Firestore fails a *whole query* when one returned document is denied, so the app's query filters and these rules are one design. Four rows check the queries `frontend/src/lib/firestore.ts` actually issues, and two check that dropping a filter correctly breaks. |

## How it talks to the emulator

`@firebase/rules-unit-testing` is Node-only, and the Python Firestore client authenticates as an
administrator — precisely the identity these tests must not have. So the script calls the emulator's
REST surface directly with `alg: none` ID tokens carrying the custom claims (`personId`, `host`,
`platformAdmin`) the rules read. That is what the Node library does underneath; the emulator does not
verify signatures. Fixtures are seeded with `Authorization: Bearer owner`, the emulator's admin
bypass, which is the only place in the file that steps around the rules.
