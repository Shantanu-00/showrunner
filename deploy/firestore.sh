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
ensure_index "media visibility+isHighlight+aestheticScore" media \
  --field-config=field-path=visibility,order=ascending \
  --field-config=field-path=curator.isHighlight,order=ascending \
  --field-config=field-path=curator.aestheticScore,order=descending
ensure_index "media albumOf+capturedAt" media \
  --field-config=field-path=albumOf,array-config=contains \
  --field-config=field-path=capturedAt,order=descending
ensure_index "bounties status+expiresAt" bounties \
  --field-config=field-path=status,order=ascending \
  --field-config=field-path=expiresAt,order=ascending
ensure_index "reels persona+version" reels \
  --field-config=field-path=persona,order=ascending \
  --field-config=field-path=version,order=descending

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
# Deny-by-default until spec 04's recompute_visibility and the S9 rules matrix land. A closed
# database that needs the API is the safe intermediate state; an open one is not.
if command -v firebase >/dev/null 2>&1 && [[ -f "${REPO_ROOT}/firestore.rules" ]]; then
  (cd "${REPO_ROOT}" && firebase deploy --only firestore:rules --project "${PROJECT_ID}" >/dev/null) \
    && note "firestore.rules deployed" \
    || note "rules deploy skipped (firebase login required) — deny-all remains in force"
else
  note "firebase CLI not available — rules unchanged (deny-all default)"
fi

echo
echo "Firestore ready. Composite index builds continue in the background (minutes on an empty DB)."
