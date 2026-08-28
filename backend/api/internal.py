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

from fastapi import APIRouter, Header, Query, Request
from fastapi.concurrency import run_in_threadpool
from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from directors.story import director
from directors.story import taste as taste_mod
from schemas.event import EventClass, EventStatus
from shared import errors, fs, internal, leases, log, oidc, tasks
from shared.auth import Principal, verify_bearer
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
    if not (principal.host_event_id or principal.platform_admin):
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
    return report


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

    for event_id, event in targets:
        tick_id = new_ulid()
        # Every Firestore call in this loop goes through the threadpool. `api` serves guests' upload
        # requests from the same process at concurrency 80, so a tick that blocked the event loop for
        # the seconds a director takes would stall every phone talking to this instance.
        lease = await run_in_threadpool(
            leases.acquire, fs.tick_ref(event_id), tick_id, ttl_seconds=TICK_LEASE_MINUTES * 60
        )
        if not lease.ok:
            # The previous tick is still running. Spec 05 §1's whole reason for the lease.
            skipped.append({"eventId": event_id, "reason": "tick_in_progress"})
            continue

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
                lastTrigger="demo" if demo else ("host" if eventId else "schedule"),
            )
        (ticked if outcome == "ok" else skipped).append(report)

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
    wrong place to check it from — a log entry cannot be read by the `/judge` page's next-tick
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
