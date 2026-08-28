"""Which events *this* instance is the wall for — per-event leader election (spec 04 §4).

The tempting shape is `max-instances=1`: one publisher process, therefore one writer, therefore no
races. It is also wrong, and wrong in a way an architect notices — it serialises every concurrent
event's playlist through a single process, which is a correctness bottleneck disguised as a scale
limit, and it contradicts the rest of the system's multi-event design (the Scheduler tick already
fans out across every live event without per-event infrastructure).

So the invariant is stated where it actually lives: **no two writers touch one event's playlist.**
Each instance runs `min-instances=1 / max-instances=5`, discovers live events, and takes a
transactional lease per event before it publishes anything. An instance that is scaled away stops
renewing, and its events are picked up by another instance inside the lease TTL — killing one
instance mid-lease affects that one event for at most two minutes and no other event at all.

Discovery is a listener, not a poll: the host presses Go Live and the wall has a program seconds
later. The renewal timer exists because a lease has to be refreshed on a clock regardless of whether
anything changed, and it doubles as the reconciliation pass for events that ended.
"""

from __future__ import annotations

import os
import threading
import uuid
from typing import Any

from shared import fs, leases, log
from shared.settings import PUBLISHER_LEASE_SECONDS, PUBLISHER_RENEW_SECONDS

from . import store
from .runner import EventPublisher


def _holder_id() -> str:
    """Identifies this process. `K_REVISION` makes a log line say which deploy holds a wall; the
    random suffix keeps two instances of the same revision distinguishable, which is the whole point
    of a lease holder id."""
    return f"{os.environ.get('K_REVISION', 'local')}:{uuid.uuid4().hex[:8]}"


class Supervisor:
    def __init__(self) -> None:
        self.holder = _holder_id()
        self._runners: dict[str, EventPublisher] = {}
        self._leases: dict[str, leases.Lease] = {}
        self._wake = threading.Event()
        self._stopped = threading.Event()
        self._discovery: Any = None
        self._thread = threading.Thread(target=self._loop, name="publisher-supervisor", daemon=True)

    # ---------------------------------------------------------------- lifecycle

    def start(self) -> None:
        self._thread.start()
        try:
            self._discovery = store.live_query().on_snapshot(lambda *_: self._wake.set())
        except Exception as exc:  # noqa: BLE001 - the renewal loop is the fallback for this
            log.error("publisher_discovery_failed", err=str(exc))
        log.info("publisher_supervisor_started", holder=self.holder)

    def stop(self) -> None:
        self._stopped.set()
        self._wake.set()
        if self._discovery is not None:
            try:
                self._discovery.unsubscribe()
            except Exception as exc:  # noqa: BLE001
                log.warn("publisher_discovery_unsubscribe_failed", err=str(exc))
        self._thread.join(timeout=5.0)
        for event_id in list(self._runners):
            self._drop(event_id, reason="shutdown")
        log.info("publisher_supervisor_stopped", holder=self.holder)

    def holds(self, event_id: str) -> bool:
        return event_id in self._runners

    def held_events(self) -> list[str]:
        """Which walls this instance is currently the writer for — the health surface's answer to
        "is leader election doing anything", and the only way to see it without a Firestore console."""
        return sorted(self._runners)

    # ---------------------------------------------------------------- the loop

    def _loop(self) -> None:
        while not self._stopped.is_set():
            try:
                self._reconcile()
            except Exception as exc:  # noqa: BLE001 - one bad pass must not end leadership
                log.error("publisher_reconcile_failed", holder=self.holder, err=str(exc))
            self._wake.wait(PUBLISHER_RENEW_SECONDS)
            self._wake.clear()

    def _reconcile(self) -> None:
        live = set(store.live_event_ids())

        for event_id in live:
            lease = leases.acquire(
                fs.publisher_lease_ref(event_id),
                self.holder,
                ttl_seconds=PUBLISHER_LEASE_SECONDS,
            )
            self._leases[event_id] = lease
            if lease.ok and event_id not in self._runners:
                runner = EventPublisher(event_id, self.holder)
                self._runners[event_id] = runner
                runner.start()
            elif not lease.ok and event_id in self._runners:
                # Another instance took it — most likely ours expired while this process was
                # throttled. Stand down immediately rather than write against a lease we lost.
                self._drop(event_id, reason=f"lease lost to {lease.blocked_by}")

        for event_id in list(self._runners):
            if event_id not in live:
                self._drop(event_id, reason="event no longer live")

    def _drop(self, event_id: str, *, reason: str) -> None:
        runner = self._runners.pop(event_id, None)
        if runner is not None:
            runner.stop()
        lease = self._leases.pop(event_id, None)
        if lease is not None and lease.ok:
            leases.release(lease, lastReason=reason)
        log.info("publisher_released", event_id=event_id, holder=self.holder, reason=reason)


#: One supervisor per process. `app.py` starts it in the FastAPI lifespan so it dies with the
#: instance, which is what makes the lease TTL the failover mechanism rather than a leak.
supervisor = Supervisor()
