"""End-to-end smoke test of the Guardian, and of the first photo ever to reach `status='indexed'`.

Companion to `smoke_upload.py` (the spine + Curator) and `smoke_faces.py` (identity). This one closes
spec 03's chain: `safety` is the last stage on a photo, so until it lands nothing in the system can be
`indexed`, and nothing that is not `indexed` can appear on a public surface. What this script proves,
in order:

  1. **The deterministic half is a decision table**, not a prompt. `workers/safety/gate.py` is a pure
     function; the hard gate, the `minor_prominent` rule, the host's sensitivity ceiling and the
     refusal default are asserted here without a network call, because that is exactly what makes
     them auditable. Explicit-content blocking is verified this way *on purpose* — the live branch
     would require creating explicit content, which this project will not do.
  2. **The judgment half runs on a real photo** through the real queue: verdict, reasons, the stored
     SafeSearch annotation, and the token cost against spec 09 §2's rail.
  3. **`status='indexed'` and `visibility='public'`** — the milestone. A Ring-2 upload of a good photo
     with a `public_ok` verdict above the aesthetic floor reaches a public surface, and every one of
     those four conditions is checked separately so a failure names itself.
  4. **The host override** (spec 03 §5.3): a host decision flips exposure through
     `recompute_visibility` and nothing else, in one transaction, and is reversible.
  5. **Surgical replay** (spec 03 §6): re-running one stage on one item, with a fresh attempt budget.

    python scripts/smoke_safety.py --event-id dev_01J...
    python scripts/smoke_safety.py --event-id dev_... --chaos 1     # inject a 500, expect a retry
    python scripts/smoke_safety.py --event-id dev_... --gate-only   # no uploads, no spend
"""

from __future__ import annotations

import argparse
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
from google.cloud.firestore_v1.base_query import FieldFilter  # noqa: E402

from schemas.common import GuardianVerdict  # noqa: E402
from schemas.guardian_out import DignityReason, GuardianOut  # noqa: E402
from services.vision import SafeSearch  # noqa: E402
from shared import fs  # noqa: E402
from shared.settings import settings  # noqa: E402
from shared.ulid import new_ulid  # noqa: E402
from workers.safety import gate  # noqa: E402

from smoke_faces import mint_host_token  # noqa: E402
from smoke_upload import (  # noqa: E402
    put_bytes,
    register_intent,
    sign_in_anonymously,
    stage_ms,
    unique_jpeg,
    wait_for_stage,
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

#: The AI-generated cast portrait from the B1 probes — a real face, no real guest (video content
#: rules). It scored 0.75 aesthetic through the Curator, which is above the 0.45 default floor, so it
#: is the fixture that can actually demonstrate a photo reaching `public`.
PHOTO = Path(__file__).resolve().parent / "risk_tests" / "artifacts" / "cast_portrait.jpg"

#: Spec 09 §2 prices the safety stage at the same ~1,548 input tokens as classify, and the queue's
#: 8/s rate is calibrated against it.
SAFETY_TOKENS_IN_RAIL = 1548

SAFE_CATEGORIES = ("adult", "racy", "violence", "medical", "spoof")


def fail(message: str) -> None:
    print(f"FAIL  {message}")
    raise SystemExit(1)


def ok(message: str) -> None:
    print(f"ok    {message}")


# ---------------------------------------------------------------- 1. the decision table


def check_gate() -> None:
    """`gate.decide` is pure — so the trust-critical branches are checked without spending a cent."""
    clean = SafeSearch()
    strict_pda = {"eventTypeProfile": {"sensitivityProfile": {"pda": "private_only"}}}
    relaxed_pda = {"eventTypeProfile": {"sensitivityProfile": {"pda": "public_ok"}}}
    conservative = {"eventTypeProfile": {"sensitivityProfile": {"attire": "conservative"}}}

    def out(verdict: GuardianVerdict, *reasons: DignityReason, ritual: bool = False) -> GuardianOut:
        return GuardianOut(verdict=verdict, reasons=list(reasons), ritualEmotion=ritual)

    cases: list[tuple[str, SafeSearch, GuardianOut | None, dict[str, Any], GuardianVerdict]] = [
        # The hard gate. `adult >= LIKELY` forces Ring 0 no matter how the model read the frame —
        # this is the row that stands in for the live explicit-content test we will not stage.
        ("adult LIKELY overrides a public_ok model verdict", SafeSearch(adult="LIKELY"),
         out(GuardianVerdict.PUBLIC_OK), relaxed_pda, GuardianVerdict.BLOCKED),
        ("adult VERY_LIKELY blocks with no model answer at all", SafeSearch(adult="VERY_LIKELY"),
         None, {}, GuardianVerdict.BLOCKED),
        ("racy LIKELY floors at private_only", SafeSearch(racy="LIKELY"),
         out(GuardianVerdict.PUBLIC_OK), relaxed_pda, GuardianVerdict.PRIVATE_ONLY),
        ("violence LIKELY floors at private_only", SafeSearch(violence="LIKELY"),
         out(GuardianVerdict.PUBLIC_OK), relaxed_pda, GuardianVerdict.PRIVATE_ONLY),
        ("racy POSSIBLE does not floor (the bar is LIKELY)", SafeSearch(racy="POSSIBLE"),
         out(GuardianVerdict.PUBLIC_OK), relaxed_pda, GuardianVerdict.PUBLIC_OK),
        # The child rule, independent of every dial.
        ("minor_prominent can never be public", clean,
         out(GuardianVerdict.PUBLIC_OK, DignityReason.MINOR_PROMINENT), relaxed_pda,
         GuardianVerdict.HOST_REVIEW),
        # The host's declared dial as a ceiling, in both directions.
        ("a private_only PDA dial clamps an observed embrace", clean,
         out(GuardianVerdict.PUBLIC_OK, DignityReason.PDA_VISIBLE), strict_pda,
         GuardianVerdict.PRIVATE_ONLY),
        ("a public_ok PDA dial does NOT clamp it", clean,
         out(GuardianVerdict.PUBLIC_OK, DignityReason.PDA_VISIBLE), relaxed_pda,
         GuardianVerdict.PUBLIC_OK),
        ("a public_ok dial cannot lift a conservative stage reading", clean,
         out(GuardianVerdict.PRIVATE_ONLY, DignityReason.DISTRESS_OUT_OF_CONTEXT), relaxed_pda,
         GuardianVerdict.PRIVATE_ONLY),
        ("a conservative attire dial clamps revealing attire", clean,
         out(GuardianVerdict.PUBLIC_OK, DignityReason.ATTIRE_REVEALING), conservative,
         GuardianVerdict.PRIVATE_ONLY),
        ("an unset dial (context_dependent) leaves the model's reading alone", clean,
         out(GuardianVerdict.PUBLIC_OK, DignityReason.ALCOHOL_VISIBLE), {},
         GuardianVerdict.PUBLIC_OK),
        # Ritual emotion: the judgment spec 03 §5.3 names as the one no rule table could make.
        ("ritual tears stay public_ok", clean,
         out(GuardianVerdict.PUBLIC_OK, ritual=True), {}, GuardianVerdict.PUBLIC_OK),
        ("distress alone goes private_only", clean,
         out(GuardianVerdict.PRIVATE_ONLY, DignityReason.DISTRESS_OUT_OF_CONTEXT), {},
         GuardianVerdict.PRIVATE_ONLY),
        # Conservative defaults.
        ("a refusal defaults to host_review, never public_ok", clean, None, {},
         GuardianVerdict.HOST_REVIEW),
        ("a model that returns `blocked` is capped at private_only", clean,
         out(GuardianVerdict.BLOCKED), {}, GuardianVerdict.PRIVATE_ONLY),
    ]

    for label, annotation, model_out, event, expected in cases:
        verdict, reasons = gate.decide(annotation, model_out, event)
        if verdict is not expected:
            fail(f"gate: {label} → {verdict.value}, expected {expected.value} (reasons={reasons})")
        print(f"  ok  {verdict.value:<13} {label}")
    ok(f"{len(cases)} gate decisions correct (pure function, no network, no spend)")


# ---------------------------------------------------------------- helpers


def wait_for_indexed(event_id: str, media_id: str, timeout: float) -> dict[str, Any]:
    """Poll until every stage has settled, reporting the stage map as it fills in."""
    ref = fs.media_ref(event_id, media_id)
    started = time.time()
    last = ""
    while time.time() - started < timeout:
        doc = ref.get().to_dict() or {}
        stages = doc.get("stages") or {}
        shown = " ".join(f"{k}={v}" for k, v in sorted(stages.items()))
        if shown != last:
            print(f"      status={doc.get('status')}  {shown}  t+{time.time() - started:.1f}s")
            last = shown
        settled = stages and all(
            state in ("done", "failed", "failed_permanent") for state in stages.values()
        )
        if settled:
            return doc
        time.sleep(1.0)
    fail(f"timed out after {timeout:.0f}s waiting for every stage to settle ({last})")
    return {}


def check_guardian(doc: dict[str, Any]) -> dict[str, Any]:
    state = (doc.get("stages") or {}).get("safety")
    if state != "done":
        reason = (doc.get("stageErrors") or {}).get("safety")
        fail(f"stages.safety={state!r}, expected 'done' (error={reason!r})")
    ok("stages.safety=done")

    guardian = doc.get("guardian") or {}
    if not guardian:
        fail("stages.safety=done but there is no `guardian` block on the document")

    verdict = guardian.get("verdict")
    if verdict not in {v.value for v in GuardianVerdict}:
        fail(f"guardian.verdict={verdict!r} is not a known verdict")
    if verdict == GuardianVerdict.BLOCKED.value:
        fail("the cast portrait was blocked by SafeSearch — that is a fixture or threshold problem")
    ok(f"verdict={verdict} reasons={guardian.get('reasons')} ritualEmotion={guardian.get('ritualEmotion')}")

    annotation = guardian.get("safeSearch") or {}
    missing = [c for c in SAFE_CATEGORIES if c not in annotation]
    if missing:
        fail(f"guardian.safeSearch is missing {missing} — the hard gate's evidence was not stored")
    ok(f"safeSearch stored: {' '.join(f'{k}={annotation[k]}' for k in SAFE_CATEGORIES)}")

    if guardian.get("modelError"):
        fail(f"the dignity pass degraded: {guardian['modelError']}")
    if guardian.get("hostDecision") is not None:
        fail("guardian.hostDecision is already set on a fresh item")

    worker, end_to_end = stage_ms(doc, "safety")
    print(f"      safety: worker {worker}ms · queued→done {end_to_end}ms")
    return guardian


def check_tokens(doc: dict[str, Any]) -> None:
    """Both Gemini stages sum into `usage`, so the rail check is against curate + safety."""
    usage = doc.get("usage") or {}
    tokens_in, tokens_out = int(usage.get("tokensIn") or 0), int(usage.get("tokensOut") or 0)
    if tokens_in <= 0:
        fail("usage.tokensIn is 0 — no model call was billed to this item")
    per_stage = tokens_in / 2.0
    drift = (per_stage - SAFETY_TOKENS_IN_RAIL) / SAFETY_TOKENS_IN_RAIL * 100
    ok(
        f"usage: {tokens_in} in / {tokens_out} out across curate+safety "
        f"(~{per_stage:.0f} per stage, {drift:+.0f}% vs the spec 09 §2 rail of {SAFETY_TOKENS_IN_RAIL})"
    )
    if drift > 15:
        fail(f"per-stage input tokens {drift:+.0f}% over the rail — the queue rates depend on it")


def review(api: str, host_token: str, event_id: str, media_id: str, decision: str) -> dict[str, Any]:
    resp = requests.post(
        f"{api}/v1/events/{event_id}/media/{media_id}/review",
        headers={"Authorization": f"Bearer {host_token}"},
        json={"decision": decision, "note": "smoke test"},
        timeout=30,
    )
    if resp.status_code != 200:
        fail(f"host review ({decision}) failed ({resp.status_code}): {resp.text[:400]}")
    return resp.json()


# ---------------------------------------------------------------- main


def main() -> int:
    cfg = settings()

    ap = argparse.ArgumentParser(description="Smoke-test the Guardian, indexing and host override.")
    ap.add_argument("--event-id", default=os.environ.get("SMOKE_EVENT_ID"))
    ap.add_argument("--api", default=os.environ.get("NEXT_PUBLIC_API_URL"))
    ap.add_argument("--file", default=None, help="use a different photo")
    ap.add_argument("--timeout", type=float, default=150.0)
    ap.add_argument("--gate-only", action="store_true", help="decision table only — no uploads")
    ap.add_argument("--chaos", type=int, default=0, metavar="N",
                    help="make the first N safety deliveries fail with a 500")
    args = ap.parse_args()

    print("── the deterministic half (spec 03 §5.3 / spec 11 §2)")
    check_gate()
    if args.gate_only:
        print("\nPASS  gate decision table only (--gate-only)")
        return 0

    api = (args.api or "").rstrip("/")
    api_key = os.environ.get("NEXT_PUBLIC_FIREBASE_API_KEY", "")
    if not args.event_id:
        fail("no event — pass --event-id or set SMOKE_EVENT_ID (see scripts/dev_event.py)")
    if not api:
        fail("no API URL — pass --api or set NEXT_PUBLIC_API_URL")
    if not api_key:
        fail("no NEXT_PUBLIC_FIREBASE_API_KEY — run ./deploy/bootstrap.sh")

    event = fs.get_event(args.event_id)
    if not event:
        fail(f"event {args.event_id} does not exist")
    photo = Path(args.file) if args.file else PHOTO
    if not photo.exists():
        fail(f"fixture missing: {photo} (run scripts/risk_tests/banana.py, or pass --file)")
    floor = float((event.get("demoConfig") or {}).get("publicFloor")
                  if (event.get("demoConfig") or {}).get("publicFloor") is not None
                  and event.get("class") == "protected_demo"
                  else event.get("publicFloor", cfg.default_public_floor))
    print(f"\n── live: event {args.event_id} (class={event.get('class')}, publicFloor={floor})")

    if args.chaos:
        if event.get("class") not in ("protected_demo", "internal_dev"):
            fail(f"--chaos needs a protected_demo/internal_dev event; this one is {event.get('class')!r}")
        fs.ops_col(args.event_id).document("chaos").set(
            {"failNext": args.chaos, "stages": ["safety"], "reason": "smoke test chaos injection"}
        )
        ok(f"ops/chaos armed: failNext={args.chaos} stage=safety")

    # ---- 1. a Ring-2 upload of a photo good enough to clear the floor
    data = unique_jpeg(photo)
    token, uid = sign_in_anonymously(api_key)
    media_id = new_ulid()
    target = register_intent(api, args.event_id, token, media_id, data, "public")
    put_bytes(target["signedUrl"], data)
    ok(f"uploaded {photo.name} ({len(data)} bytes) as Ring 2 (public consent): {media_id}")

    budget = args.timeout + args.chaos * 60.0
    doc = wait_for_stage(args.event_id, media_id, "safety", budget)
    guardian = check_guardian(doc)

    if args.chaos:
        attempts = int((doc.get("attempts") or {}).get("safety") or 0)
        if attempts <= args.chaos:
            fail(f"attempts.safety={attempts} after {args.chaos} injected failures — chaos did not fire")
        ok(f"survived {args.chaos} injected 500s: attempts.safety={attempts}, stage still done")
        fs.ops_col(args.event_id).document("chaos").delete()
        ok("ops/chaos cleared")

    # ---- 2. the milestone: every stage done → indexed, and a public surface becomes reachable
    doc = wait_for_indexed(args.event_id, media_id, budget)
    stages = doc.get("stages") or {}
    if doc.get("status") != "indexed":
        fail(f"status={doc.get('status')!r} with stages {stages} — the derived status did not land")
    ok(f"status=indexed (all of {', '.join(sorted(stages))} done) — the first photo to get here")
    check_tokens(doc)

    aesthetic = float((doc.get("curator") or {}).get("aestheticScore") or 0.0)
    verdict = guardian.get("verdict")
    expect_public = (
        verdict == GuardianVerdict.PUBLIC_OK.value
        and aesthetic >= floor
        and not (doc.get("subjectVetoes") or [])
        and not event.get("publicFrozen")
    )
    visibility = doc.get("visibility")
    if expect_public and visibility != "public":
        fail(f"visibility={visibility!r} but consent=2, verdict={verdict}, aesthetic={aesthetic} >= {floor}")
    if not expect_public and visibility == "public":
        fail(f"visibility=public with verdict={verdict} aesthetic={aesthetic} floor={floor} — a gate leaked")
    ok(f"visibility={visibility} (ring 2 · {verdict} · aesthetic {aesthetic:.2f} vs floor {floor})")
    if not expect_public:
        print("      NOTE  not public, and correctly so — the gate that held it is named above")

    # ---- 3. the host override, and its reversal (spec 03 §5.3)
    host_token = mint_host_token(args.event_id, api_key)
    result = review(api, host_token, args.event_id, media_id, GuardianVerdict.PRIVATE_ONLY.value)
    if result.get("visibility") != "pool":
        fail(f"host set private_only but visibility={result.get('visibility')!r}, expected pool")
    after = fs.media_ref(args.event_id, media_id).get().to_dict() or {}
    if (after.get("guardian") or {}).get("verdict") != verdict:
        fail("the host decision overwrote the model's verdict — it must sit beside it, for the audit")
    ok(f"host private_only → visibility=pool; model verdict {verdict} preserved for the audit trail")

    result = review(api, host_token, args.event_id, media_id, GuardianVerdict.PUBLIC_OK.value)
    if result.get("visibility") != ("public" if expect_public else "pool"):
        fail(f"host public_ok → visibility={result.get('visibility')!r}, expected reversal")
    ok(f"host public_ok → visibility={result.get('visibility')} (reversible, one writer, one transaction)")

    audits = [
        snap.to_dict() or {}
        for snap in fs.ops_col(args.event_id)
        .where(filter=FieldFilter("mediaId", "==", media_id))
        .stream()
    ]
    decisions = [a for a in audits if a.get("kind") == "moderation_decision"]
    if len(decisions) < 2:
        fail(f"expected 2 moderation_decision ops records, found {len(decisions)}")
    unresolved = [a for a in decisions if not a.get("resolved")]
    if unresolved:
        fail(
            f"{len(unresolved)} moderation audit record(s) are unresolved — a decision the host just "
            "made would sit on their alert badge as outstanding work"
        )
    ok(f"{len(decisions)} moderation decisions recorded in ops/ as resolved (audit feed, not badge)")

    # ---- 4. surgical replay of one stage (spec 03 §6)
    before_attempts = int((after.get("attempts") or {}).get("safety") or 0)
    resp = requests.post(
        f"{api}/v1/events/{args.event_id}/admin/replay/{media_id}",
        headers={"Authorization": f"Bearer {host_token}"},
        params={"stage": "safety"},
        timeout=30,
    )
    if resp.status_code != 200:
        fail(f"replay failed ({resp.status_code}): {resp.text[:400]}")
    ok(f"replay queued: {resp.json()}")
    replayed = wait_for_stage(args.event_id, media_id, "safety", args.timeout)
    if (replayed.get("stages") or {}).get("safety") != "done":
        fail(f"replayed safety stage settled as {(replayed.get('stages') or {}).get('safety')!r}")
    attempts_now = int((replayed.get("attempts") or {}).get("safety") or 0)
    if attempts_now != 1:
        fail(f"attempts.safety={attempts_now} after a replay — the counter was not reset to a fresh budget")
    ok(f"stage re-ran and settled done with a fresh attempt budget (was {before_attempts}, now {attempts_now})")
    if (fs.media_ref(args.event_id, media_id).get().to_dict() or {}).get("status") != "indexed":
        fail("status is no longer indexed after a replay")
    ok("status=indexed after the replay — idempotent re-run, same answer")

    print()
    print(f"PASS  {args.event_id}/{media_id} guardian + indexing + host override + replay")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
