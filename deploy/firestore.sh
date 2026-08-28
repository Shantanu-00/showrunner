#!/usr/bin/env bash
# Firestore database + the spec 09 §3 index inventory. Idempotent.
#
# Applied with gcloud, not `firebase deploy`, so a fresh machine needs nothing but ADC.
# `firestore.indexes.json` is the committed declarative copy of the same inventory — keep the two
# in step; both come from spec 09 §3.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

step "Firestore database"
if gcloud firestore databases describe --database='(default)' --project "${PROJECT_ID}" >/dev/null 2>&1; then
  note "(default) native database exists"
else
  gcloud firestore databases create \
    --location "${REGION}" --type firestore-native --project "${PROJECT_ID}" >/dev/null
  note "(default) native database created in ${REGION}"
fi

ensure_index() {
  # ALREADY_EXISTS is the expected outcome on a re-run and is not an error.
  # --async because the six builds are serial otherwise: ~2 min each on an empty database, which
  # pushes a plain `up.sh` re-run past ten minutes for indexes nothing in this session queries yet.
  local label="$1" group="$2"
  shift 2
  local out
  if out="$(gcloud firestore indexes composite create --async \
      --collection-group="${group}" --query-scope=collection --project "${PROJECT_ID}" \
      "$@" 2>&1)"; then
    note "index created: ${label}"
  elif grep -qiE 'already exists|ALREADY_EXISTS' <<< "${out}"; then
    note "index exists: ${label}"
  else
    echo "${out}" >&2
    return 1
  fi
}

step "Composite indexes (spec 09 §3)"
# Kiosk + album ordering: capturedAt for chronology, uploadedAt for the `just_in` strip.
ensure_index "media visibility+status+capturedAt" media \
  --field-config=field-path=visibility,order=ascending \
  --field-config=field-path=status,order=ascending \
  --field-config=field-path=capturedAt,order=descending
ensure_index "media visibility+status+uploadedAt" media \
  --field-config=field-path=visibility,order=ascending \
  --field-config=field-path=status,order=ascending \
  --field-config=field-path=uploadedAt,order=descending
# Highlights and the private album both carry one more filter than spec 09 §3's list implies, and
# both for the same reason (S9): spec 04 §2 requires `status=='indexed'` on *every* public-surface
# query, and a subject may not read a Ring-0 item they appear in. A Firestore query whose filters do
# not guarantee the security rule fails entirely rather than skipping the document, so the filters and
# the rules are one design — which makes these the indexes those queries actually need.
ensure_index "media visibility+status+isHighlight+aestheticScore" media \
  --field-config=field-path=visibility,order=ascending \
  --field-config=field-path=status,order=ascending \
  --field-config=field-path=curator.isHighlight,order=ascending \
  --field-config=field-path=curator.aestheticScore,order=descending
ensure_index "media visibility+albumOf+capturedAt" media \
  --field-config=field-path=visibility,order=ascending \
  --field-config=field-path=albumOf,array-config=contains \
  --field-config=field-path=capturedAt,order=descending
# "My uploads" orders by createdAt, whose *single-field* index is disabled below (hotspot); a
# composite led by uploaderUid spreads the writes by uploader and serves the query.
ensure_index "media uploaderUid+createdAt" media \
  --field-config=field-path=uploaderUid,order=ascending \
  --field-config=field-path=createdAt,order=descending
# The host's moderation queue: everything the Guardian routed to `host_review` (spec 03 §5.3).
ensure_index "media guardian.verdict+uploadedAt" media \
  --field-config=field-path=guardian.verdict,order=ascending \
  --field-config=field-path=uploadedAt,order=descending
# The Story Director's evidence sample (spec 05 §2's drift signal) and its pending bounty
# submissions: the most recent indexed items, whatever their visibility. Deliberately *not* the
# `visibility+status+uploadedAt` composite above — a drift signal computed only from photos that
# happened to clear the public gate would measure consent, not what the event looks like.
ensure_index "media status+uploadedAt" media \
  --field-config=field-path=status,order=ascending \
  --field-config=field-path=uploadedAt,order=descending
ensure_index "bounties status+expiresAt" bounties \
  --field-config=field-path=status,order=ascending \
  --field-config=field-path=expiresAt,order=ascending
ensure_index "reels persona+version" reels \
  --field-config=field-path=persona,order=ascending \
  --field-config=field-path=version,order=descending
# The Reel Director's SELECT step (spec 06 §3 step 1): eligible media ordered by aesthetic rather than
# by time, because a gallery is browsing and a reel is *choosing*. Deliberately not the
# `+isHighlight+` index above — filtering on isHighlight would make a reel impossible at an event where
# the Curator has not called anything a highlight yet, and the aesthetic floor is the right bar.
ensure_index "media visibility+status+aestheticScore" media \
  --field-config=field-path=visibility,order=ascending \
  --field-config=field-path=status,order=ascending \
  --field-config=field-path=curator.aestheticScore,order=descending
# Spec 06 §7's consent interlock: which published reels contain a photograph that just lost public
# eligibility (shared/reels.py, fired from the one writer of `visibility`).
ensure_index "reels assetManifest+status" reels \
  --field-config=field-path=assetManifest,array-config=contains \
  --field-config=field-path=status,order=ascending

step "Vector index (spec 09 §3): faces.embedding, 512-d"
# A vector index declares dimension only; COSINE is a `find_nearest(distance_measure=...)`
# argument at query time (shared/faces.py), not an index property — the index just has to exist.
# Double-quoted, not single: the unescaped {..} is bash brace-expansion syntax and unquoted (or
# single-quoted-then-split) it silently fans out into two broken flags instead of one.
ensure_index "faces embedding (512-d vector)" faces \
  "--field-config=field-path=embedding,vector-config={dimension=512,flat}"

step "Single-field exemption: media.createdAt"
# ULID doc IDs already spread writes, but an indexed monotonically-increasing timestamp is its own
# 500 writes/s hotspot — and nothing orders by createdAt (capturedAt does that job).
gcloud firestore indexes fields update createdAt --async \
  --collection-group=media --disable-indexes --project "${PROJECT_ID}" >/dev/null
note "media.createdAt indexes disabled"

step "Security rules"
# GUARD (added S9, after the 2026-08-27 incident — HANDOFF §9). `firebase deploy --only
# firestore:rules` pushes whatever is on disk, and it has no idea a file was edited to be permissive
# for a local emulator run. That is how production spent 98 minutes with `allow read, write: if true`.
# So: refuse to deploy a rules file that grants an unconditional write, or that opens the recursive
# wildcard. `ALLOW_PERMISSIVE_RULES=1` overrides it deliberately and loudly; nothing sets that by
# accident. Note what is *not* matched — `match /kiosk/{document} { allow read: if true; }` is spec 09
# §3 verbatim and legitimate, so the guard looks for unconditional *writes* and for the catch-all.
if grep -nE 'allow[^:]*write[^:]*:[[:space:]]*if[[:space:]]+true' "${REPO_ROOT}/firestore.rules" >/dev/null 2>&1 \
   || grep -nE 'match[[:space:]]*/\{document=\*\*\}' -A3 "${REPO_ROOT}/firestore.rules" | grep -qE 'if[[:space:]]+true'; then
  if [[ "${ALLOW_PERMISSIVE_RULES:-0}" == "1" ]]; then
    note "WARNING: permissive rules deployed on purpose (ALLOW_PERMISSIVE_RULES=1)"
  else
    echo "REFUSING to deploy firestore.rules: it grants an unconditional write." >&2
    grep -nE 'if[[:space:]]+true' "${REPO_ROOT}/firestore.rules" >&2 || true
    echo "If this is a leftover emulator edit, stash it. To override: ALLOW_PERMISSIVE_RULES=1" >&2
    exit 1
  fi
fi

if command -v firebase >/dev/null 2>&1 && [[ -f "${REPO_ROOT}/firestore.rules" ]]; then
  (cd "${REPO_ROOT}" && firebase deploy --only firestore:rules --project "${PROJECT_ID}" >/dev/null) \
    && note "firestore.rules deployed" \
    || note "rules deploy skipped (firebase login required) — deny-all remains in force"
else
  note "firebase CLI not available — rules unchanged (deny-all default)"
fi

echo
echo "Firestore ready. Composite index builds continue in the background (minutes on an empty DB)."
