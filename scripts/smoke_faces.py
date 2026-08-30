"""End-to-end smoke test of the Face Indexer + claim flow (spec 03 §5.2, spec 02 §3).

Companion to `smoke_upload.py`, not a replacement — that script proves the upload → intake →
Curator spine; this one proves the two S5 acceptance criteria the run sheet names explicitly:

  1. **"selfie → my album fills."** Upload a real photo containing a face (an unclaimed cluster
     lands on the media doc) → enroll with a selfie of that same face → the face-level claim
     (spec 03 §5.2) links the cluster's faces to the new person → `albumOf` on the photo now
     contains their `personId`.
  2. **"VIP claim → host approval path exists."** Seed a VIP person whose stored selfie embedding
     matches the same face, then attempt the identical enrollment again. The impersonation guard
     (spec 02 §3) must hold it for host review rather than silently granting VIP access; a host
     token then approves it and the guest's uid is linked.

Needs a deployed `api` *and* `worker-face` (`WORKER_FACE_URL` set — this script calls the face
embedding path directly, the same way `api` does, to derive the VIP's seed embedding).

    python scripts/smoke_faces.py --event-id dev_01J...
    python scripts/smoke_faces.py --offline               # decision building blocks only, no network

**What `--offline` does and does not cover.** `faces_lib.is_ambiguous` and `PersonHit.protected`
(`shared/faces.py`) are genuinely pure — imported and exercised here unmodified, the same way
`smoke_safety.py --gate-only` exercises `workers/safety/gate.py::decide`. The *selection* between
`CLAIM_SIZE`/`HOST_APPROVAL` (a brand-new person's claim) and between
`PROTECTED_PERSON`/`AMBIGUOUS_MATCH`/`HOST_APPROVAL` (a claim matching someone already enrolled) is
not: both are one-line ternaries inline inside `api/identity.py`'s Firestore-writing handlers
(`_create_person_and_claim`, `_hold_identity_match`) rather than a standalone function like
`gate.decide`. `identity.py` is outside this script's ownership, so that selection is deliberately
*not* re-implemented here as a shadow copy of the real ternary — a copy that could drift from the
real thing silently is worse than no offline coverage for it at all. `check_claim_logic` below
covers everything that genuinely is separable; extracting the two ternaries into pure functions
(`workers/safety/gate.py`'s pattern) would close the remaining gap, but that is a production-code
change and not this script's call to make.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import os
import sys
import time
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import requests  # noqa: E402
import google.auth  # noqa: E402
import google.auth.iam  # noqa: E402
import google.auth.jwt  # noqa: E402
import google.auth.transport.requests  # noqa: E402

from schemas.person import Tier  # noqa: E402
from shared import faces as faces_lib, fs, internal as face_internal  # noqa: E402
from shared.settings import settings  # noqa: E402
from shared.ulid import new_ulid  # noqa: E402

from smoke_upload import (  # noqa: E402
    put_bytes,
    register_intent,
    sign_in_anonymously,
    unique_jpeg,
    wait_for_stage,
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FACE_PHOTO = Path(__file__).resolve().parent / "risk_tests" / "artifacts" / "cast_portrait.jpg"


def fail(message: str) -> None:
    print(f"FAIL  {message}")
    raise SystemExit(1)


def ok(message: str) -> None:
    print(f"ok    {message}")


#: Firebase custom-token audience — fixed by the Identity Toolkit spec, not a config value.
_CUSTOM_TOKEN_AUDIENCE = (
    "https://identitytoolkit.googleapis.com/google.identity.identitytoolkit.v1.IdentityToolkit"
)


def _mint_custom_token(uid: str, claims: dict[str, Any]) -> str:
    """Hand-build a Firebase custom-token JWT, signed via IAM signBlob impersonation.

    `firebase_admin.auth.create_custom_token` needs a service-account *credential* (a key file —
    disallowed) or the GCE metadata server (absent on this Windows dev box) to find a signer. The
    deployed services never hit this: their ambient Cloud Run credential resolves it natively.
    Off Cloud Run, `google.auth.iam.Signer` gets the same signature the same way `shared/gcs.py`
    signs GCS URLs — by impersonating `SIGNER_SA_EMAIL`, no key file either place. This is dev-only
    scaffolding standing in for S10's not-yet-built host magic link; the shape it produces is
    exactly what `firebase_admin.auth.create_custom_token` would have produced.
    """
    signer_email = settings().signer_sa_email
    if not signer_email:
        fail("SIGNER_SA_EMAIL is not set — run ./deploy/sa.sh")
    source_creds, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    request = google.auth.transport.requests.Request()
    signer = google.auth.iam.Signer(request, source_creds, signer_email)
    now = int(time.time())
    payload = {
        "iss": signer_email,
        "sub": signer_email,
        "aud": _CUSTOM_TOKEN_AUDIENCE,
        "iat": now,
        "exp": now + 3600,
        "uid": uid,
        "claims": claims,
    }
    return google.auth.jwt.encode(signer, payload).decode("utf-8")


def mint_host_token(event_id: str, api_key: str) -> str:
    """A verified ID token carrying `host: eventId` — minted directly, since S10's host magic
    link doesn't exist yet. Exercises the same claim `_require_host` reads, nothing weaker."""
    custom_token = _mint_custom_token(f"smoke-host-{new_ulid()}", {"host": event_id})
    resp = requests.post(
        f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithCustomToken?key={api_key}",
        json={"token": custom_token, "returnSecureToken": True},
        timeout=30,
    )
    if resp.status_code != 200:
        fail(f"host custom-token sign-in failed ({resp.status_code}): {resp.text[:300]}")
    return resp.json()["idToken"]


def embed_photo(path: Path) -> list[float]:
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    try:
        body = face_internal.embed_selfie(b64, max_faces=1)
    except face_internal.FaceServiceError as exc:
        fail(f"worker-face /embed failed: {exc}")
    faces = body.get("faces") or []
    if not faces:
        fail(f"no face detected in {path} — fixture or detector regression")
    return faces[0]["embedding"]


def seed_vip(event_id: str, embedding: list[float], display_name: str) -> str:
    person_id = new_ulid()
    now = dt.datetime.now(dt.timezone.utc)
    # Face template in its own document (`enrollments/{personId}`), exactly where the enrollment
    # endpoint puts it — no client rule grants that collection, so a person doc can stay readable
    # for display names and tiers without publishing everyone's biometric (S9, shared/fs.py).
    fs.enrollment_ref(event_id, person_id).set(
        {"personId": person_id, "embedding": embedding, "createdAt": now}
    )
    fs.person_ref(event_id, person_id).set(
        {
            "personId": person_id,
            "displayName": display_name,
            "tier": int(Tier.NAMED_VIP),
            "hostEnrolled": False,
            # A person the host put there is approved by construction — `workers/face` refuses to
            # auto-link faces to an unapproved person, so a seeded VIP without this flag would never
            # accrete an album at all (S15).
            "claimApproved": True,
            "featured": False,
            "consent": {"selfieEnrolled": True, "enrolledAt": now, "retentionNoticeShown": True},
            "createdAt": now,
        }
    )
    fs.person_private_ref(event_id, person_id).set({"uidLinks": [], "tasteProfile": {}})
    return person_id


def enroll(api: str, token: str, event_id: str, selfie_b64: str, display_name: str | None) -> dict[str, Any]:
    resp = requests.post(
        f"{api}/v1/events/{event_id}/people",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "selfie": selfie_b64,
            "displayName": display_name,
            "biometricConsent": True,
            "retentionNoticeShown": True,
        },
        timeout=60,
    )
    if resp.status_code != 200:
        fail(f"POST /people failed ({resp.status_code}): {resp.text[:400]}")
    return resp.json()


# ---------------------------------------------------------------- 0. the claim-decision building blocks


def check_claim_logic() -> None:
    """Everything behind a claim's `holdReason` (spec 02 §3) that is pure enough to check with no
    network and no spend — see the module docstring for exactly where that separability ends."""
    cfg = settings()

    # spec 02 §3, verbatim: "the face indexer matches at tau_match (0.45), looser than tau_claim
    # (0.60)". The impersonation guard depends on that gap in both directions — closing it would make
    # an ordinary indexing match and a claim attempt the same threshold.
    if not (0.0 < cfg.tau_match < cfg.tau_claim < 1.0):
        fail(
            f"tau_match={cfg.tau_match} tau_claim={cfg.tau_claim} — "
            "expected 0 < tau_match < tau_claim < 1 (spec 02 §3)"
        )
    ok(f"tau_match={cfg.tau_match} < tau_claim={cfg.tau_claim} (spec 02 §3's ordering holds)")

    if not (0.0 < cfg.claim_ambiguity_margin < 1.0):
        fail(f"claim_ambiguity_margin={cfg.claim_ambiguity_margin} — expected between 0 and 1")
    if cfg.claim_review_threshold <= 0:
        fail(f"claim_review_threshold={cfg.claim_review_threshold} — expected a positive face count")
    ok(
        f"claim_ambiguity_margin={cfg.claim_ambiguity_margin} "
        f"claim_review_threshold={cfg.claim_review_threshold}"
    )

    def hit(
        person_id: str, similarity: float, *, tier: int = 3, host_enrolled: bool = False
    ) -> faces_lib.PersonHit:
        return faces_lib.PersonHit(person_id, similarity, {"tier": tier, "hostEnrolled": host_enrolled})

    # `is_ambiguous` only ever looks at the top two, and the margin is exclusive: a gap exactly equal
    # to the margin does not trigger it (`shared/faces.py`'s own `<`, not `<=`). Cases stay a clear
    # 0.01 either side of the margin rather than exactly on it, since "exactly on a float boundary" is
    # not a real invariant to pin down — cosine similarities are never that precise in practice.
    margin = cfg.claim_ambiguity_margin
    ambiguity_cases: list[tuple[str, list[faces_lib.PersonHit], bool]] = [
        ("no hits at all", [], False),
        ("a single hit has nothing to be ambiguous with", [hit("p1", 0.90)], False),
        ("top two clearly apart", [hit("p1", 0.90), hit("p2", 0.90 - margin - 0.05)], False),
        (
            "top two just outside the margin — not ambiguous",
            [hit("p1", 0.90), hit("p2", 0.90 - margin - 0.01)],
            False,
        ),
        (
            "top two just inside the margin — ambiguous",
            [hit("p1", 0.90), hit("p2", 0.90 - margin + 0.01)],
            True,
        ),
        ("twins: an identical top two", [hit("p1", 0.92), hit("p2", 0.92)], True),
    ]
    for label, hits, expected in ambiguity_cases:
        got = faces_lib.is_ambiguous(hits)
        if got is not expected:
            fail(f"is_ambiguous: {label} -> {got}, expected {expected}")
        print(f"  ok  ambiguous={got!s:<5} {label}")
    ok(f"{len(ambiguity_cases)} is_ambiguous cases correct (pure function, no network, no spend)")

    # `PersonHit.protected`: tier <= 2 (Principal / InnerCircle / NamedVIP, spec 11 §3) or
    # hostEnrolled — the two signals spec 02 §3 says a match must never silently grant.
    protection_cases: list[tuple[str, faces_lib.PersonHit, bool]] = [
        ("tier 0 (Principal) is always protected", hit("p", 0.9, tier=0), True),
        ("tier 1 (InnerCircle) is always protected", hit("p", 0.9, tier=1), True),
        ("tier 2 (NamedVIP) is always protected", hit("p", 0.9, tier=2), True),
        ("tier 3 (Guest), not host-enrolled, is not protected", hit("p", 0.9, tier=3), False),
        ("tier 3 (Guest) but host-enrolled IS protected", hit("p", 0.9, tier=3, host_enrolled=True), True),
    ]
    for label, ph, expected in protection_cases:
        got = ph.protected
        if got is not expected:
            fail(f"PersonHit.protected: {label} -> {got}, expected {expected}")
        print(f"  ok  protected={got!s:<5} {label}")
    ok(f"{len(protection_cases)} PersonHit.protected cases correct (pure property, no network, no spend)")

    print(
        "  note  holdReason selection itself (CLAIM_SIZE/HOST_APPROVAL and "
        "PROTECTED_PERSON/AMBIGUOUS_MATCH/HOST_APPROVAL) is inline in api/identity.py, not a "
        "standalone function — see the module docstring for why it is not re-tested here."
    )


def main() -> int:
    # Before the parser, not after: this populates os.environ from .env (settings.py's loader),
    # which the flag defaults below read — same ordering rule as smoke_upload.py.
    settings()

    ap = argparse.ArgumentParser(description="Smoke-test the Face Indexer + claim flow.")
    ap.add_argument("--event-id", default=os.environ.get("SMOKE_EVENT_ID"))
    ap.add_argument("--api", default=os.environ.get("NEXT_PUBLIC_API_URL"))
    ap.add_argument("--timeout", type=float, default=90.0)
    ap.add_argument("--offline", action="store_true", help="claim-decision building blocks only — no network")
    args = ap.parse_args()

    print("── the claim-decision building blocks (spec 02 §3)")
    check_claim_logic()
    if args.offline:
        print("\nPASS  claim-decision building blocks only (--offline)")
        return 0
    print()

    api = (args.api or "").rstrip("/")
    api_key = os.environ.get("NEXT_PUBLIC_FIREBASE_API_KEY", "")
    event_id = args.event_id
    if not event_id:
        fail("no event — pass --event-id or set SMOKE_EVENT_ID (see scripts/dev_event.py)")
    if not api:
        fail("no API URL — pass --api or set NEXT_PUBLIC_API_URL")
    if not api_key:
        fail("no NEXT_PUBLIC_FIREBASE_API_KEY — run ./deploy/bootstrap.sh")
    if not settings().face_url:
        fail("WORKER_FACE_URL is not set — deploy worker-face first (deploy/up.sh)")
    if not FACE_PHOTO.exists():
        fail(f"fixture missing: {FACE_PHOTO}")

    event = fs.get_event(event_id)
    if not event:
        fail(f"event {event_id} does not exist")
    print(f"event {event_id}  status={event.get('status')}")

    # The uploaded copy is made byte-unique per run (one corner pixel) so a second run against the
    # same event is not silently deduped into a no-op; the selfie stays the original file, since it
    # never touches GCS and one pixel is invisible to the embedder either way.
    data = unique_jpeg(FACE_PHOTO)
    selfie_b64 = base64.b64encode(FACE_PHOTO.read_bytes()).decode("ascii")

    # ---- 1. upload a photo containing a face, wait for the faces stage
    token, _uid = sign_in_anonymously(api_key)
    media_id = new_ulid()
    target = register_intent(api, event_id, token, media_id, data, "pool")
    put_bytes(target["signedUrl"], data)
    ok(f"photo uploaded: {media_id}")

    doc = wait_for_stage(event_id, media_id, "faces", args.timeout)
    state = (doc.get("stages") or {}).get("faces")
    if state != "done":
        reason = (doc.get("stageErrors") or {}).get("faces")
        fail(f"stages.faces={state!r}, expected 'done' (error={reason!r})")
    faces = doc.get("faces") or []
    if not faces:
        fail("stages.faces=done but zero faces indexed from a photo containing a face")
    ok(f"stages.faces=done, {len(faces)} face(s) indexed (unclaimed clusters: "
       f"{[f.get('clusterId') for f in faces]})")

    # ---- 2. selfie enrollment on the same face — held, then approved (spec 02 §3.1)
    #
    # No enrollment links anything on its own any more, whatever the face count: the host approves
    # every album (S15, `api/identity.py`'s module docstring), because "this claim is small so it is
    # probably honest" is exactly the assumption that let an anonymous visitor enroll with a photo of
    # a tier-3 guest and receive her album. So the criterion this step proves is now two-legged:
    # enrollment holds and grants nothing, and *approval* is what fills the album.
    enroll_token, enroll_uid = sign_in_anonymously(api_key)
    body = enroll(api, enroll_token, event_id, selfie_b64, "Smoke Guest")
    if body["outcome"] != "held_for_review":
        fail(f"expected outcome=held_for_review — no enrollment self-grants, got {body!r}")
    person_id = body["personId"]
    audit_id = body["claimId"]
    ok(f"enrolled personId={person_id} held as claim {audit_id} "
       f"topSimilarity={body['topSimilarity']:.3f}")

    audit = fs.claim_audit_ref(event_id, audit_id).get().to_dict() or {}
    if audit.get("status") != "held" or audit.get("method") != "enroll":
        fail(f"claimAudits/{audit_id} = {audit} — expected status=held method=enroll")
    if not audit.get("faceIds"):
        fail(f"claimAudits/{audit_id} carries no faceIds — approval could not replay the link")
    ok(f"claimAudits entry held with faceIds for {len(audit['faceIds'])} media")

    pre = fs.media_ref(event_id, media_id).get().to_dict() or {}
    if person_id in (pre.get("albumOf") or []):
        fail(f"albumOf={pre.get('albumOf')} already contains {person_id} — a held claim linked faces")
    ok("no face linked while the claim is held — the hold is real, not cosmetic")

    # The review queue the host console reads, and the endpoint that makes a hold resolvable.
    host_token = mint_host_token(event_id, api_key)
    queue = requests.get(
        f"{api}/v1/events/{event_id}/claims",
        headers={"Authorization": f"Bearer {host_token}"},
        timeout=30,
    )
    if queue.status_code != 200:
        fail(f"GET /claims failed ({queue.status_code}): {queue.text[:400]}")
    cards = {c["claimId"]: c for c in queue.json().get("claims") or []}
    if audit_id not in cards:
        fail(f"held claim {audit_id} is not in the host's review queue: {list(cards)}")
    card = cards[audit_id]
    if not str(card.get("selfieUrl") or "").endswith(f"/claims/{audit_id}/selfie"):
        fail(f"review card carries no fetchable selfie URL: {card!r}")
    ok(f"review queue shows the claim: holdReason={card['holdReason']} "
       f"exemplars={len(card['exemplars'])} selfieUrl set")

    approved = requests.post(
        f"{api}/v1/events/{event_id}/claims/{audit_id}/review",
        headers={"Authorization": f"Bearer {host_token}"},
        json={"decision": "approve"},
        timeout=30,
    )
    if approved.status_code != 200:
        fail(f"host review failed ({approved.status_code}): {approved.text[:400]}")
    ok(f"host approved claim {audit_id}: linkedFaces={approved.json()['linkedFaces']}")

    refreshed = fs.media_ref(event_id, media_id).get().to_dict() or {}
    if person_id not in (refreshed.get("albumOf") or []):
        fail(f"albumOf={refreshed.get('albumOf')} does not contain {person_id} — album did not fill")
    ok(f"albumOf={refreshed.get('albumOf')} — host approval filled the album")

    person = fs.person_ref(event_id, person_id).get().to_dict() or {}
    # `uidLinks` moved into the deny-all `private/` subcollection with the event boundary (S15) — the
    # person document stayed member-readable, and a uid↔human map is not something a member may read.
    private = fs.person_private_ref(event_id, person_id).get().to_dict() or {}
    if not person.get("claimApproved") or enroll_uid not in (private.get("uidLinks") or []):
        fail(
            f"people/{person_id} = {person} private={private} — expected claimApproved=True "
            "and the enrolling uid linked"
        )
    ok("person marked claimApproved — worker-face may now auto-link their later photos")

    # ---- 3. the same face, matched against a seeded VIP — held for host review (spec 02 §3)
    #
    # A *separate* event namespace, deliberately: step 2 just enrolled this exact photo as an
    # ordinary person, so testing the VIP guard in the *same* event would make the top-2 matches
    # (the VIP and that ordinary person) a genuine tie on an identical embedding — a real but
    # degenerate case this fixture can't tell apart from the protected-match case it's meant to
    # exercise. `claimAudits`/`people` are event-scoped subcollections with no parent-doc
    # requirement, so a synthetic event id needs no `dev_event.py` setup to be write-valid.
    vip_event_id = f"smoke-vip-{new_ulid()}"
    vip_embedding = embed_photo(FACE_PHOTO)
    vip_id = seed_vip(vip_event_id, vip_embedding, "Smoke VIP")
    ok(f"seeded VIP person {vip_id} (tier=NAMED_VIP) in isolated event {vip_event_id}")

    vip_token, vip_uid = sign_in_anonymously(api_key)
    vip_body = enroll(api, vip_token, vip_event_id, selfie_b64, None)
    if vip_body["outcome"] != "pending_host_approval":
        fail(f"expected outcome=pending_host_approval matching a VIP, got {vip_body!r}")
    claim_id = vip_body["claimId"]
    ok(f"VIP-matching selfie held: claimId={claim_id} topSimilarity={vip_body['topSimilarity']:.3f}")

    held = fs.claim_audit_ref(vip_event_id, claim_id).get().to_dict() or {}
    if held.get("status") != "held" or held.get("holdReason") != "protected_person":
        fail(f"claimAudits/{claim_id} = {held} — expected status=held holdReason=protected_person")
    ok("claimAudits entry recorded as held, host-visible and reviewable")

    # host approves
    host_token = mint_host_token(vip_event_id, api_key)
    resp = requests.post(
        f"{api}/v1/events/{vip_event_id}/claims/{claim_id}/review",
        headers={"Authorization": f"Bearer {host_token}"},
        json={"decision": "approve"},
        timeout=30,
    )
    if resp.status_code != 200:
        fail(f"host review failed ({resp.status_code}): {resp.text[:400]}")
    review = resp.json()
    if review["status"] != "approved" or review["personId"] != vip_id:
        fail(f"unexpected review result: {review}")
    ok(f"host approved claim {claim_id} -> personId={vip_id}")

    vip_doc = fs.person_private_ref(vip_event_id, vip_id).get().to_dict() or {}
    if vip_uid not in (vip_doc.get("uidLinks") or []):
        fail(f"uidLinks={vip_doc.get('uidLinks')} does not contain the approved uid {vip_uid}")
    ok("VIP person's uidLinks now includes the approved guest uid — approval path fully wired")

    print()
    print(f"PASS  {event_id} face indexer + claim flow")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
