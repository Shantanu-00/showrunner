"""One event's wall: recompute the program on push, and keep doing it while we hold the lease.

`recompute()` is the whole job and it is a plain function, callable from three places — a Firestore
listener callback, the fallback timer, and `POST /recompute` when a Scheduler tick nudges us. All
three go through the same lease check, so "who is allowed to write this event's playlist" is answered
in exactly one place no matter what triggered the write.

`EventPublisher` is the push half: a listener per input (media, recent uploads, bounties, reels, and
the event document itself) that does nothing except wake a worker thread. The work happens on that
thread rather than inside the callback for two reasons — a slow build must not block the gRPC watch
stream that delivers the next change, and a burst of twenty uploads has to collapse into one rebuild
rather than twenty (spec 04 §4 recomputes "on triggers", and a batch upload is one trigger).

Resilience worth naming, because the wall going stale is the failure everyone in the room sees: if a
watch stream dies and the SDK cannot re-establish it, the fallback timer still rebuilds every five
minutes (spec 04 §4's "every 5 min as fallback"), and a Cloud Scheduler tick can force a rebuild from
outside the process entirely. There is no single point whose failure freezes the show.
"""

from __future__ import annotations

import datetime as dt
import threading
import time
from typing import Any, Callable

from shared import fs, leases, log
from shared.settings import (
    KIOSK_DEBOUNCE_SECONDS,
    KIOSK_FALLBACK_SECONDS,
    PUBLISHER_LEASE_SECONDS,
)

from . import program, store


def recompute(
    event_id: str,
    *,
    holder: str,
    trigger: str,
    event: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Rebuild and (if it changed) publish one event's kiosk playlist.

    Returns a small report rather than raising on the expected outcomes — not the leader, event no
    longer live, nothing changed — because all three are normal and the caller (a tick, a listener)
    should log them, not treat them as errors.
    """
    started = time.monotonic()
    context = store.event_context(event_id, event)
    if context is None:
        return {"status": "no_event", "eventId": event_id}
    status = str(context.event.get("status") or "")
    if status not in store.PUBLISHED_STATUSES:
        return {"status": "not_live", "eventId": event_id, "eventStatus": status}

    lease = leases.acquire(
        fs.publisher_lease_ref(event_id),
        holder,
        ttl_seconds=PUBLISHER_LEASE_SECONDS,
    )
    if not lease.ok:
        return {"status": "not_leader", "eventId": event_id, "holder": lease.blocked_by}

    current = store.playlist(event_id)
    premiere = store.premiere_reel(event_id, current.get("premieredReelIds") or [])
    built = program.build(
        store.candidates(event_id),
        now=dt.datetime.now(dt.timezone.utc),
        active_stage_id=context.active_stage_id,
        previous_stage_id=context.previous_stage_id,
        theme=context.theme,
        premiere_reel_id=premiere,
        takeover_bounty_id=store.takeover_bounty(event_id),
    )
    written = store.publish(
        event_id, built, trigger=trigger, holder=holder, premiered=premiere
    )

    ms = int((time.monotonic() - started) * 1000)
    log.line(
        "playlist",
        event_id=event_id,
        trigger=trigger,
        changed=written.changed,
        revision=written.revision,
        slots=len(built.slots),
        heroes=built.hero_count,
        stage_id=built.active_stage_id,
        premiere=premiere,
        ms=ms,
    )
    return {
        "status": "published" if written.changed else "unchanged",
        "eventId": event_id,
        "revision": written.revision,
        "slots": len(built.slots),
        "heroCount": built.hero_count,
        "activeStageId": built.active_stage_id,
        "premieredReelId": premiere if written.changed else None,
        "ms": ms,
    }


class EventPublisher:
    """Listeners + one worker thread for a single event we hold the lease on."""

    def __init__(self, event_id: str, holder: str) -> None:
        self.event_id = event_id
        self.holder = holder
        self._wake = threading.Event()
        self._stopped = threading.Event()
        self._lock = threading.Lock()
        self._reasons: list[str] = []
        self._watches: list[Any] = []
        self._thread = threading.Thread(
            target=self._loop, name=f"publisher-{event_id}", daemon=True
        )

    # ---------------------------------------------------------------- lifecycle

    def start(self) -> None:
        self._thread.start()
        self._attach()
        log.info("publisher_event_started", event_id=self.event_id, holder=self.holder)

    def stop(self) -> None:
        self._stopped.set()
        for watch in self._watches:
            try:
                watch.unsubscribe()
            except Exception as exc:  # noqa: BLE001 - shutdown must not raise
                log.warn("publisher_unsubscribe_failed", event_id=self.event_id, err=str(exc))
        self._watches.clear()
        self._wake.set()
        self._thread.join(timeout=5.0)
        log.info("publisher_event_stopped", event_id=self.event_id)

    def touch(self, reason: str) -> None:
        with self._lock:
            if reason not in self._reasons:
                self._reasons.append(reason)
        self._wake.set()

    # ---------------------------------------------------------------- internals

    def _attach(self) -> None:
        """One watch per input. The event document is included so a stage change or a Go Live
        re-themes the wall inside spec 04 §6's 5-second bound without waiting for a photo."""
        sources: list[tuple[str, Callable[[], Any]]] = [
            ("media", lambda: store.public_query(self.event_id)),
            ("upload", lambda: store.recent_query(self.event_id)),
            ("bounty", lambda: store.bounty_query(self.event_id)),
            ("reel", lambda: store.reel_query(self.event_id)),
            ("event", lambda: fs.event_ref(self.event_id)),
        ]
        for label, build_source in sources:
            try:
                self._watches.append(build_source().on_snapshot(self._callback(label)))
            except Exception as exc:  # noqa: BLE001 - one dead watch must not cost the others
                log.error("publisher_watch_failed", event_id=self.event_id, source=label, err=str(exc))

    def _callback(self, label: str) -> Callable[..., None]:
        def on_snapshot(*_args: Any) -> None:
            self.touch(label)

        return on_snapshot

    def _drain(self) -> str:
        with self._lock:
            reasons, self._reasons = self._reasons, []
        return ",".join(reasons) if reasons else "fallback"

    def _loop(self) -> None:
        while not self._stopped.is_set():
            woken = self._wake.wait(KIOSK_FALLBACK_SECONDS)
            if self._stopped.is_set():
                break
            self._wake.clear()
            if woken:
                # Collapse the rest of the burst: a 20-photo batch arrives as up to 20 snapshots.
                time.sleep(KIOSK_DEBOUNCE_SECONDS)
                self._wake.clear()
            trigger = self._drain()
            try:
                recompute(self.event_id, holder=self.holder, trigger=trigger)
            except Exception as exc:  # noqa: BLE001 - the loop outlives any single failure
                log.error(
                    "publisher_recompute_failed",
                    event_id=self.event_id,
                    trigger=trigger,
                    err=str(exc),
                )
                # Back off a little so a persistent failure does not spin on the fallback path.
                time.sleep(2.0)
