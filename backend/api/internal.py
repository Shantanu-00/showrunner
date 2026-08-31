"""`/internal/tick` — the heartbeat of the control plane, and the one endpoint nobody presses.

This is the 40%-criterion surface: *"intercept and complete a multi-step background workflow without
human intervention."* Two Cloud Scheduler jobs call it (spec 09 §2) — `director-tick` every 2 minutes
for real events, `director-tick-demo` at `* * * * *` scoped to the demo event — and every invocation
does the same three things per event: take a lease, do the work, release the lease.

Four decisions here are load-bearing:

1. **The lease is released when the tick ends, not when it expires.** Spec 05 §1 gives
   `ticks/{eventId}` a 5-minute TTL, and holding it that long would throttle the cadence instead of
   protecting it — a 900 ms tick would block the next four scheduled ticks. The TTL is a crash
   backstop; the release is the normal path. That is also what makes a double-fire a no-op rather
   than a double-issued bounty (spec 05 §1's actual requirement).
2. **The 30-second demo cadence is server-side.** Cloud Scheduler's cron floor is one minute, so each
   demo invocation enqueues one Cloud Task at +30 s hitting this same endpoint (spec 09 §2/§5). An
   interleaved task never enqueues another, so the fan-out is exactly one extra tick per minute, and
   a dropped interleave self-heals on the next minute. The rejected alternative was a console-driven
   loop, which on camera is indistinguishable from pressing a button.
3. **`api` is the one public service, so this endpoint authenticates its caller in the handler.**
   A Google-signed OIDC token from `sa-scheduler` or `sa-tasks` (`shared/oidc.py`), or a Firebase host
   token for spec 05 §1's "Run director now" fallback button — which is scoped to a single event the
   caller actually hosts, because a host may trigger their own event and nothing else.
4. **A tick never fails because one event's work did.** Cloud Scheduler retries a non-2xx, and a
   retry storm across every live event because one event has a broken document would be a worse
   outage than the stale wall it was trying to fix. Per-event failures are reported in the response
   body and logged; the response itself is 200.

The Story Director (spec 05's Validate → Expire → Arm → Ledger → Reason → Act) is the body of
`_do_work`. It runs *inside* the lease taken here and takes none of its own; the lease, the fan-out,
the auth and the cadence are unchanged from the session that built them.
"""

from __future__ import annotations

import base64
import datetime as dt
import json
from typing import Any

from fastapi import APIRouter, Depends, Header, Path, Query, Request
from fastapi.concurrency import run_in_threadpool
from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from directors.story import director
from directors.story import resolve as resolve_mod
from directors.story import taste as taste_mod
from directors.story import world as world_mod
from schemas.event import EventClass, EventStatus
from shared import coverage, errors, fs, internal, leases, log, oidc, tasks
from shared.auth import Principal, caller, verify_bearer
from shared.settings import DEMO_INTERLEAVE_SECONDS, TICK_LEASE_MINUTES, settings
from shared.ulid import new_ulid

router = APIRouter(prefix="/internal", tags=["internal"])

#: Statuses whose events get a tick. `wrapping` is included because the wrap-up report and the finale
#: reel are the last things the director does (spec 08 §2) — an event that stops being ticked the
#: moment the host presses "wrap" would never produce them.
TICKED_STATUSES = (EventStatus.LIVE.value, EventStatus.WRAPPING.value)


def _service_emails() -> set[str]:
    cfg = settings()
    return {cfg.scheduler_sa_email, cfg.tasks_sa_email}


def _authorize(request: Request, authorization: str | None) -> tuple[str, Principal | None]:
    """Return `(caller_kind, principal)`. Raises 401/403 for anyone else.

    Firebase ID tokens and Google OIDC tokens are told apart by their issuer before either is
    verified, so neither verifier is ever handed a token of the other kind (which fails with a
    confusing error rather than a clear one).
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise errors.unauthorized("NO_TOKEN", "this endpoint requires a bearer token")
    token = authorization.split(" ", 1)[1].strip()

    if _issuer(token).endswith("accounts.google.com"):
        try:
            claims = oidc.verify(
                token,
                allowed_emails=_service_emails(),
                expected_host=(request.url.hostname or ""),
            )
        except oidc.InvalidServiceToken as exc:
            raise errors.forbidden("NOT_ALLOWED", str(exc)) from exc
        return str(claims.get("email") or "service"), None

    principal = verify_bearer(authorization)
    if not (principal.host_event_ids or principal.platform_admin):
        raise errors.forbidden("HOST_ONLY", "only a host or the platform admin may run a tick")
    return "host", principal


def _issuer(token: str) -> str:
    """The unverified `iss` claim — used only to choose a verifier, never to trust anything."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return str(json.loads(base64.urlsafe_b64decode(payload)).get("iss") or "")
    except Exception:  # noqa: BLE001 - an unparseable token is somebody else's problem to report
        return ""


def _targets(demo: bool, event_id: str | None, principal: Principal | None) -> list[tuple[str, dict]]:
    """Which events this invocation is responsible for.

    The Scheduler job is **not** per-event (spec 05 §1): events going live or wrapped need no infra
    change, only a status flip. The demo job filters `class=='protected_demo'` in Python rather than
    in the query — there are at most a handful of live events by construction (spec 11 §1's hard cap
    is 3 public ones), so a composite index for a filter that saves nothing would be dead weight.
    """
    if event_id:
        event = fs.get_event(event_id)
        if event is None:
            raise errors.not_found("NO_EVENT", "unknown event")
        if principal is not None and not (
            principal.is_host_of(event_id) or principal.platform_admin
        ):
            raise errors.forbidden("HOST_ONLY", "this action requires the host of that event")
        return [(event_id, event)]

    query = (
        fs.db()
        .collection("events")
        .where(filter=FieldFilter("status", "in", list(TICKED_STATUSES)))
    )
    found: list[tuple[str, dict]] = []
    for snap in query.stream():
        event = snap.to_dict() or {}
        if demo and event.get("class") != EventClass.PROTECTED_DEMO.value:
            continue
        found.append((snap.id, event))
    return found


# ---------------------------------------------------------------- the work


async def _do_work(event_id: str, event: dict[str, Any], *, tick_id: str) -> dict[str, Any]:
    """What one tick does for one event, under its lease.

    Two things, in this order and for that reason:

    **The Story Director** (spec 05 §1's Validate → Expire → Arm → Ledger → Reason → Act,
    `directors/story/director.py`). It runs *inside* the lease this endpoint already holds and takes
    none of its own — a second lease would deadlock against the first, and extending the first would
    throttle the cadence it exists to protect (HANDOFF §4.20).

    **Then the wall.** Spec 04 §4 lists "every 5 min as fallback" among the publisher's recompute
    triggers, and delivering that from the tick rather than from inside the publisher is what makes it
    survive the publisher being scaled to zero — during the judging month the Scheduler is the only
    thing still running on a timer. The nudge comes *after* the director so that a bounty escalated to
    a kiosk takeover, an announcement or an auto-advanced stage is on the screen at the end of the same
    tick that decided it, rather than up to two minutes later.

    **Then taste memos** (spec 07 §2, bonus +0.2) — whichever guests just crossed another multiple of
    15 reactions get a fresh Gemma memo. Last on purpose: it is the one step here that is genuinely
    off the critical path (nothing downstream gates on it, unlike the wall), so it runs after the two
    steps that are.

    A director failure is reported and does not stop the wall refresh; a publisher failure is reported
    and does not undo a bounty; a taste-memo failure does neither. None fails the tick (see this
    module's fourth design note).
    """
    report: dict[str, Any] = {"eventId": event_id, "tickId": tick_id}

    try:
        report["director"] = await director.run_tick(event_id, event, tick_id=tick_id)
    except Exception as exc:  # noqa: BLE001 - one event's director must not stop its wall or the fleet
        log.error("tick_director_failed", event_id=event_id, tick_id=tick_id, err=str(exc))
        report["director"] = {"status": "failed", "error": str(exc)[:300]}
        fs.ops_alert(
            event_id,
            "director_failed",
            f"the story director failed on this tick: {str(exc)[:300]}",
            severity="error",
            tickId=tick_id,
        )

    try:
        report["publisher"] = await run_in_threadpool(
            internal.nudge_publisher, event_id, reason="tick"
        )
    except internal.PublisherError as exc:
        # A stale wall is not a reason to fail a tick, and definitely not a reason to lose a bounty
        # that has already been issued.
        log.warn("tick_publisher_nudge_failed", event_id=event_id, err=str(exc))
        report["publisher"] = {"status": "unreachable", "error": str(exc)[:200]}

    try:
        memos = await taste_mod.run_pending(event_id)
        report["taste"] = {"memosWritten": len(memos)}
    except Exception as exc:  # noqa: BLE001 - a taste-memo cycle failing must not affect the director or the wall
        log.warn("tick_taste_memo_failed", event_id=event_id, tick_id=tick_id, err=str(exc))
        report["taste"] = {"status": "failed", "error": str(exc)[:200]}

    # The world model, last and for the same reasons as the taste memos above: it explains rather than
    # decides, so a slow or failed distillation must not delay the wall refresh or the bounty budget
    # that have already landed. It reads the coverage shards the director's own LEDGER step just
    # fetched — one extra read, not a second aggregation — and only calls a model once every
    # `WORLD_MODEL_EVERY_N_PHOTOS` photos, so most ticks it is a single document read and a comparison.
    try:
        snap = await world_mod.run_if_due(event_id, event, coverage.read(event_id))
        report["world"] = (
            {"distilled": True, "photos": snap.total, "settings": len(snap.scenes)}
            if snap
            else {"distilled": False}
        )
    except Exception as exc:  # noqa: BLE001 - see above; `run_if_due` already swallows, this is belt-and-braces
        log.warn("tick_world_model_failed", event_id=event_id, tick_id=tick_id, err=str(exc))
        report["world"] = {"status": "failed", "error": str(exc)[:200]}

    # The off-topic resolver (`directors/story/resolve.py`) — last of all, and for the same reason as
    # the two steps above: it only ever explains a photo the ranking has already ranked, so nothing
    # about the wall or the director's own guardrails may wait on it.
    try:
        resolved = await resolve_mod.run_pending(event_id)
        report["resolve"] = {"checked": resolved.checked, "noted": len(resolved.noted)}
    except Exception as exc:  # noqa: BLE001 - must not fail the tick
        log.warn("tick_resolve_failed", event_id=event_id, tick_id=tick_id, err=str(exc))
        report["resolve"] = {"status": "failed", "error": str(exc)[:200]}
    return report


# ---------------------------------------------------------------- one event's tick


async def _tick_one(
    event_id: str, event: dict[str, Any], *, trigger: str
) -> tuple[dict[str, Any], bool]:
    """Lease → work → release, for exactly one event. Returns `(report, ok)`.

    Extracted so the scheduled fan-out and the tour page's manual override run the *same* code
    under the *same* lease. A second path that ticked an event without taking `ticks/{eventId}` would
    void spec 05 §1's only guarantee — that two concurrent ticks cannot double-issue a bounty — and it
    would do so on the one event a judge is looking at.
    """
    tick_id = new_ulid()
    # Every Firestore call here goes through the threadpool. `api` serves guests' upload requests from
    # the same process at concurrency 80, so a tick that blocked the event loop for the seconds a
    # director takes would stall every phone talking to this instance.
    lease = await run_in_threadpool(
        leases.acquire, fs.tick_ref(event_id), tick_id, ttl_seconds=TICK_LEASE_MINUTES * 60
    )
    if not lease.ok:
        # The previous tick is still running. Spec 05 §1's whole reason for the lease.
        return {"eventId": event_id, "reason": "tick_in_progress"}, False

    outcome, report = "ok", {"eventId": event_id, "tickId": tick_id}
    try:
        report = await _do_work(event_id, event, tick_id=tick_id)
    except Exception as exc:  # noqa: BLE001 - one event's failure is not the fleet's
        outcome = "error"
        report["error"] = str(exc)[:300]
        log.error("tick_failed", event_id=event_id, tick_id=tick_id, err=str(exc))
        await run_in_threadpool(
            fs.ops_alert,
            event_id,
            "director_tick_failed",
            f"a director tick failed: {str(exc)[:300]}",
            severity="error",
            tickId=tick_id,
        )
    finally:
        await run_in_threadpool(
            leases.release,
            lease,
            lastTickAt=fs.SERVER_TIMESTAMP,
            lastOutcome=outcome,
            lastTickId=tick_id,
            lastTrigger=trigger,
        )
    return report, outcome == "ok"


# ---------------------------------------------------------------- the endpoint


@router.post("/tick")
async def tick(
    request: Request,
    demo: bool = Query(False, description="scope to protected_demo events (the demo cadence job)"),
    interleave: bool = Query(False, description="set on the +30 s task; never re-interleaves"),
    eventId: str | None = Query(None, description="host fallback: tick exactly one event"),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    started = dt.datetime.now(dt.timezone.utc)
    caller_kind, principal = _authorize(request, authorization)

    targets = await run_in_threadpool(_targets, demo, eventId, principal)
    ticked: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    trigger = "demo" if demo else ("host" if eventId else "schedule")
    for event_id, event in targets:
        report, ok = await _tick_one(event_id, event, trigger=trigger)
        (ticked if ok else skipped).append(report)

    interleaved = _interleave(demo, interleave, request)

    ms = int((dt.datetime.now(dt.timezone.utc) - started).total_seconds() * 1000)
    await run_in_threadpool(
        _pulse,
        mode="demo" if demo else "schedule",
        interleave=interleave,
        caller=caller_kind,
        events=len(targets),
        ticked=len(ticked),
        ms=ms,
    )
    log.line(
        "tick",
        caller=caller_kind,
        mode="demo" if demo else "schedule",
        interleave=interleave or None,
        events=len(targets),
        ticked=len(ticked),
        skipped=len(skipped) or None,
        interleaved=interleaved or None,
        ms=ms,
    )
    return {
        "ok": True,
        "mode": "demo" if demo else "schedule",
        "interleave": interleave,
        "events": len(targets),
        "ticked": ticked,
        "skipped": skipped,
        "interleavedIn": DEMO_INTERLEAVE_SECONDS if interleaved else None,
        "ms": ms,
    }


def _public_base(request: Request) -> str:
    """This service's externally reachable origin — which `request.url` does not give you.

    Cloud Run terminates TLS at the front end and forwards plain HTTP to the container, so
    `request.url.scheme` is `http` on every request. Cloud Tasks then refuses the enqueue outright:
    *"HttpRequest.url must start with 'https://' for request with HttpRequest.authorization_header"* —
    it will not attach an OIDC token to a cleartext target, which is the right call and a confusing
    400 to receive. The host header is trustworthy for this purpose (the audience check in
    `_authorize` compares against the same value), so the scheme is the only thing to correct.
    """
    host = request.headers.get("host") or request.url.netloc
    scheme = "https"
    if host.split(":")[0] in ("localhost", "127.0.0.1"):
        scheme = request.url.scheme  # local uvicorn, no proxy in front of it
    return f"{scheme}://{host}"


def _pulse(**fields: Any) -> None:
    """One heartbeat document, written by every tick whether or not it had anything to do.

    Autonomy that cannot be checked is a claim rather than a property, and Cloud Logging is the
    wrong place to check it from — a log entry cannot be read by the tour page's next-tick
    countdown (EXECUTION-PLAN §7e) and it cannot be asserted by a smoke test without log-reading
    credentials. So the tick leaves a mark in Firestore: `platform/tickPulse` is what proves the
    Scheduler is firing, what the demo's 30-second interleave is measured against, and what a
    countdown will eventually read. It is a *root* document — a tick that found zero live events is
    exactly as important to record as one that found five, and there is no event to hang it under.

    Never raises: a heartbeat that could fail a tick would be worse than no heartbeat.
    """
    try:
        fs.platform_doc("tickPulse").set(
            {
                **fields,
                "lastTickAt": fs.SERVER_TIMESTAMP,
                "ticks": firestore.Increment(1),
            },
            merge=True,
        )
    except Exception as exc:  # noqa: BLE001
        log.warn("tick_pulse_write_failed", err=str(exc))


def _interleave(demo: bool, interleave: bool, request: Request) -> bool:
    """Queue the +30 s half of the demo cadence (spec 09 §2). Returns whether one was queued.

    Guarded on `not interleave` so the interleaved task cannot queue its own successor: with that
    guard the fan-out is exactly one extra tick per Scheduler minute, and without it every tick
    would spawn a tick forever. The audience is pinned to the bare service URL because the target
    carries a query string and the receiving handler compares the audience against its own host.
    """
    if not demo or interleave:
        return False
    cfg = settings()
    base = _public_base(request)
    try:
        queued = tasks.enqueue(
            cfg.priority_queue,
            f"{base}/internal/tick?demo=1&interleave=1",
            {"reason": "demo_cadence_interleave"},
            stage="tick",
            schedule_in_seconds=DEMO_INTERLEAVE_SECONDS,
            audience=base,
        )
    except Exception as exc:  # noqa: BLE001 - a missed interleave self-heals next minute
        log.warn("tick_interleave_failed", err=str(exc))
        return False
    return queued is not None


# ---------------------------------------------------------------- the tour page's manual override

demo_router = APIRouter(prefix="/v1/events", tags=["demo"])

#: How often the tour page's override may actually spend a director call, per event. Not
#: spec-pinned; flagged in HANDOFF §9. Each forced tick is a real `gemini-3.7-flash` call, so an
#: unthrottled button on a public page is a money path. Ninety seconds is short enough that a judge who
#: presses it gets a tick rather than a refusal, and long enough that holding the button down cannot
#: outspend the scheduled cadence it stands in for.
DEMO_FORCE_MIN_SECONDS = 90


@demo_router.post("/{eventId}/demo/tick")
async def force_demo_tick(
    eventId: str = Path(min_length=1, max_length=128),
    principal: Principal = Depends(caller),
) -> dict[str, Any]:
    """Run one director tick now, on the demo event only.

    This exists for dead air and nothing else. EXECUTION-PLAN §7e row 11 is explicit that a judge
    pressing a button seconds before reading *"without human intervention"* is a rules-§4 "must
    function as depicted" contradiction — so the tour page presents the Cloud Scheduler countdown
    as the cadence and labels this, on screen, as a manual override. What makes it safe to expose at
    all is that it is bounded three ways:

    - **`class == 'protected_demo'` only.** A real host's event cannot be ticked from here by anyone,
      including its own host, who already has spec 05 §1's scoped fallback on `/internal/tick`.
    - **Rate-limited per event**, because a forced tick spends a real model call.
    - **It goes through `_tick_one`**, so it takes the same `ticks/{eventId}` lease the scheduled tick
      takes. A judge hammering this cannot double-issue a bounty; they get `tick_in_progress`.

    Authenticated only in the sense that every guest is: an anonymous Firebase token is enough, the
    same identity the tour already holds by step 2. There is nothing here worth a stronger gate that
    the class check does not already provide.
    """
    event = await run_in_threadpool(fs.get_event, eventId)
    if not event:
        raise errors.not_found("NO_EVENT", "unknown event")
    if event.get("class") != EventClass.PROTECTED_DEMO.value:
        # Deliberately the same shape of refusal a wrong-event request gets, and deliberately explicit
        # about why: this endpoint is documented on a public page, so a confusing 403 is worse than a
        # clear one. It leaks only that this event is not the demo event.
        raise errors.forbidden("DEMO_ONLY", "this endpoint only runs on the demo event")
    if event.get("status") not in TICKED_STATUSES:
        return {"ran": False, "message": f"the demo event is {event.get('status')}, not live"}

    ref = fs.tick_ref(eventId)
    now = dt.datetime.now(dt.timezone.utc)
    snap = await run_in_threadpool(ref.get)
    last = (snap.to_dict() or {}).get("lastForcedAt") if snap.exists else None
    if isinstance(last, dt.datetime) and (now - last).total_seconds() < DEMO_FORCE_MIN_SECONDS:
        wait = int(DEMO_FORCE_MIN_SECONDS - (now - last).total_seconds())
        return {"ran": False, "message": f"just forced one — try again in {wait}s"}

    report, ok = await _tick_one(eventId, event, trigger="judge_override")
    # Recorded after the tick rather than before, so a tick that could not take the lease does not
    # start the cooldown. `lastForcedAt` lives on the lease document because that is already the one
    # place per event that records tick history, and it is client-unreadable (root collection, no rule).
    await run_in_threadpool(ref.set, {"lastForcedAt": now}, True)
    log.line("tick_forced", event=eventId, uid=principal.uid, ok=ok)
    return {
        "ran": ok,
        "message": None if ok else str(report.get("reason") or report.get("error") or "tick skipped"),
        "bountiesIssued": len((report.get("act") or {}).get("issued") or []) if ok else 0,
    }
