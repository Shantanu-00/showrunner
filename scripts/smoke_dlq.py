"""End-to-end smoke test of budget exhaustion — the opposite test from every other `--chaos` flag.

`--chaos N` on `smoke_upload.py` / `smoke_safety.py` injects N failures the retry policy is *expected
to win*: the queue redelivers, the item eventually lands `done`, nothing is lost. That proves
resilience, not the failure path. This script proves the other half of spec 03 §6's taxonomy: a
transient failure that keeps failing until `MAX_STAGE_ATTEMPTS` (5, `shared/settings.py`) is spent
lands the stage `failed`, the media `quarantined`, and an `ops/` alert at `error` severity — and that
`POST …/admin/replay/{mediaId}?stage=` recovers it afterwards.

Reuses `shared/chaos.py` exactly the way `smoke_upload.py --chaos` does: `ops/chaos` armed with
`failNext` >= `MAX_STAGE_ATTEMPTS` guarantees every delivery fails instead of the usual "the first N
fail, then a real attempt lands" — this is the one case where exhausting the budget *is* the point.
Same guard as every other `--chaos` caller: `protected_demo` / `internal_dev` events only.

Cloud Tasks backoff (`deploy/queues.sh`: min 10s, max 300s, 4 doublings) means the five deliveries
take a couple of minutes to land for real — this script waits on the actual queue rather than
simulating it, same as every other smoke script's convention of exercising the real thing.

    python scripts/smoke_dlq.py --event-id dev_...
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import requests  # noqa: E402
from google.cloud.firestore_v1.base_query import FieldFilter  # noqa: E402

from shared import fs  # noqa: E402
from shared.settings import MAX_STAGE_ATTEMPTS, settings  # noqa: E402
from shared.ulid import new_ulid  # noqa: E402

from smoke_faces import mint_host_token  # noqa: E402
from smoke_upload import (  # noqa: E402
    put_bytes,
    register_intent,
    set_chaos,
    sign_in_anonymously,
    unique_jpeg,
    wait_for_stage,
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

#: Any small real JPEG works — chaos fires before the Curator ever reads the bytes, so a generated
#: gradient (the same fixture `smoke_upload.py` uses without `--file`) is exactly as good as a photo.
PHOTO = Path(__file__).resolve().parent / "risk_tests" / "artifacts" / "cast_portrait.jpg"

REPLAY_STAGE = "curate"

_ALLOWED_CLASSES = ("protected_demo", "internal_dev")


def fail(message: str) -> None:
    print(f"FAIL  {message}")
    raise SystemExit(1)


def ok(message: str) -> None:
    print(f"ok    {message}")


def check_quarantined(doc: dict[str, Any]) -> None:
    state = (doc.get("stages") or {}).get(REPLAY_STAGE)
    if state != "failed":
        reason = (doc.get("stageErrors") or {}).get(REPLAY_STAGE)
        fail(f"stages.{REPLAY_STAGE}={state!r}, expected 'failed' (error={reason!r})")
    ok(f"stages.{REPLAY_STAGE}=failed after exhausting the retry budget")

    attempts = int((doc.get("attempts") or {}).get(REPLAY_STAGE) or 0)
    if attempts < MAX_STAGE_ATTEMPTS:
        fail(f"attempts.{REPLAY_STAGE}={attempts}, expected >= MAX_STAGE_ATTEMPTS ({MAX_STAGE_ATTEMPTS})")
    ok(f"attempts.{REPLAY_STAGE}={attempts} (budget was {MAX_STAGE_ATTEMPTS})")

    if doc.get("status") != "quarantined":
        fail(f"status={doc.get('status')!r}, expected 'quarantined'")
    ok("status=quarantined — the item stopped rather than silently vanishing")


def check_ops_alert(event_id: str, media_id: str) -> None:
    alerts = [
        snap.to_dict() or {}
        for snap in fs.ops_col(event_id).where(filter=FieldFilter("mediaId", "==", media_id)).stream()
    ]
    matches = [a for a in alerts if a.get("kind") == "stage_failed" and a.get("severity") == "error"]
    if not matches:
        fail(
            f"no stage_failed/error ops alert for {media_id} among {len(alerts)} record(s) — "
            "budget exhaustion must be on the record, not just a status flip"
        )
    if not any(not a.get("resolved") for a in matches):
        fail("the stage_failed alert is marked resolved — quarantine is exactly the unresolved case")
    ok(f"ops/ alert recorded: kind=stage_failed severity=error resolved=False ({len(matches)} match(es))")


def replay(api: str, host_token: str, event_id: str, media_id: str) -> None:
    resp = requests.post(
        f"{api}/v1/events/{event_id}/admin/replay/{media_id}",
        headers={"Authorization": f"Bearer {host_token}"},
        params={"stage": REPLAY_STAGE},
        timeout=30,
    )
    if resp.status_code != 200:
        fail(f"replay failed ({resp.status_code}): {resp.text[:400]}")
    ok(f"replay queued: {resp.json()}")


def main() -> int:
    cfg = settings()

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--event-id", default=os.environ.get("SMOKE_EVENT_ID"))
    ap.add_argument("--api", default=os.environ.get("NEXT_PUBLIC_API_URL"))
    ap.add_argument(
        "--timeout",
        type=float,
        default=420.0,
        help="seconds to wait for all 5 chaos-injected deliveries to exhaust (queue backoff alone is ~150s)",
    )
    args = ap.parse_args()

    api = (args.api or "").rstrip("/")
    api_key = os.environ.get("NEXT_PUBLIC_FIREBASE_API_KEY", "")
    if not args.event_id:
        fail("no event — pass --event-id or set SMOKE_EVENT_ID (see scripts/dev_event.py)")
    if not api:
        fail("no API URL — pass --api or set NEXT_PUBLIC_API_URL")
    if not api_key:
        fail("no NEXT_PUBLIC_FIREBASE_API_KEY — run ./deploy/bootstrap.sh")
    if not PHOTO.exists():
        fail(f"fixture missing: {PHOTO}")

    event = fs.get_event(args.event_id)
    if not event:
        fail(f"event {args.event_id} does not exist")
    if event.get("class") not in _ALLOWED_CLASSES:
        fail(f"this needs a protected_demo/internal_dev event; {args.event_id} is {event.get('class')!r}")
    print(f"event {args.event_id}  status={event.get('status')}  class={event.get('class')}")

    set_chaos(args.event_id, REPLAY_STAGE, MAX_STAGE_ATTEMPTS)

    token, _uid = sign_in_anonymously(api_key)
    media_id = new_ulid()
    data = unique_jpeg(PHOTO)
    target = register_intent(api, args.event_id, token, media_id, data, "pool")
    put_bytes(target["signedUrl"], data)
    ok(f"photo uploaded: {media_id} (every {REPLAY_STAGE} delivery will fail — chaos armed at "
       f"{MAX_STAGE_ATTEMPTS})")

    print(f"\n── waiting for the retry budget to exhaust (~{MAX_STAGE_ATTEMPTS} deliveries, queue backoff)")
    doc = wait_for_stage(args.event_id, media_id, REPLAY_STAGE, args.timeout)
    check_quarantined(doc)
    check_ops_alert(args.event_id, media_id)

    # Disarm before replaying — chaos would otherwise fail the replay's own delivery too, and this
    # script is testing recovery, not re-exhausting the same budget a second time.
    fs.ops_col(args.event_id).document("chaos").delete()
    ok("ops/chaos cleared")

    print(f"\n── replaying {REPLAY_STAGE} (spec 03 §6)")
    host_token = mint_host_token(args.event_id, api_key)
    replay(api, host_token, args.event_id, media_id)

    replayed = wait_for_stage(args.event_id, media_id, REPLAY_STAGE, args.timeout)
    state = (replayed.get("stages") or {}).get(REPLAY_STAGE)
    if state != "done":
        reason = (replayed.get("stageErrors") or {}).get(REPLAY_STAGE)
        fail(f"stages.{REPLAY_STAGE}={state!r} after replay, expected 'done' (error={reason!r})")
    ok(f"stages.{REPLAY_STAGE}=done after replay — a fresh attempt budget, not the exhausted one")

    if replayed.get("status") == "quarantined":
        fail(f"status is still quarantined after a successful replay: {replayed}")
    ok(f"status={replayed.get('status')} — no longer quarantined")

    print()
    print(f"PASS  {args.event_id}: {media_id} exhausted its retry budget, quarantined with an alert, "
          "and recovered through replay")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
