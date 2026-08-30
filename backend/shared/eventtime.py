"""Event-local calendar math — the one place "Day N" is ever computed (spec 13).

`Event.startsOn`/`endsOn` are ISO **local dates** in `Event.timezone`, and a day index is always
derived from them at read time, never stored: a host correcting the start date mid-trip must not
leave a stale day number on any document, prompt or pixel. Every renderer that says "Day 2" says it
through this module (the frontend mirror is `frontend/src/lib/eventTime.ts`).

Degradation is the contract: an event with no `startsOn` (every event created before spec 13, or a
host who skipped the field) gets `day_index() is None` and the time-only rendering every surface had
before multi-day existed. A malformed date or an unknown timezone degrades the same way rather than
failing a tick — calendar labels are presentation, and presentation must never be the reason a
bounty was not issued.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any, Mapping
from zoneinfo import ZoneInfo

_UTC = dt.timezone.utc


def _parse_date(value: Any) -> dt.date | None:
    if isinstance(value, dt.date) and not isinstance(value, dt.datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return dt.date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _zone(name: Any) -> dt.tzinfo:
    try:
        return ZoneInfo(str(name))
    except Exception:  # noqa: BLE001 - a bad tz name degrades to UTC, never fails a caller
        return _UTC


@dataclass(frozen=True)
class EventCalendar:
    """The calendar view of one event: its timezone and (optionally) its date span."""

    tz: dt.tzinfo
    starts_on: dt.date | None
    ends_on: dt.date | None

    @classmethod
    def of(cls, event: Mapping[str, Any] | None) -> "EventCalendar":
        doc = event or {}
        return cls(
            tz=_zone(doc.get("timezone") or "UTC"),
            starts_on=_parse_date(doc.get("startsOn")),
            ends_on=_parse_date(doc.get("endsOn")),
        )

    @property
    def dated(self) -> bool:
        return self.starts_on is not None

    def day_count(self) -> int | None:
        if self.starts_on is None or self.ends_on is None:
            return None
        return max(1, (self.ends_on - self.starts_on).days + 1)

    def local(self, at: dt.datetime) -> dt.datetime:
        """`at` in the event's wall clock. Naive datetimes are read as UTC, matching `fs`/Firestore."""
        if at.tzinfo is None:
            at = at.replace(tzinfo=_UTC)
        return at.astimezone(self.tz)

    def day_index(self, at: dt.datetime) -> int | None:
        """1-based day number of `at`, or None when the event is undated. Deliberately unclamped:
        Day 0 / Day 7 of a 5-day event are honest answers about an out-of-range instant, and the
        caller decides whether that is an error or just a late upload."""
        if self.starts_on is None:
            return None
        return (self.local(at).date() - self.starts_on).days + 1

    def day_label(self, at: dt.datetime) -> str:
        """`"Day 2"`, or `""` when undated — safe to interpolate unconditionally."""
        index = self.day_index(at)
        return f"Day {index}" if index is not None else ""

    def stamp(self, at: dt.datetime) -> str:
        """A compact event-local stamp for prompts and logs: `"Day 2 Tue 14:05"` (or `"Tue 14:05"`)."""
        local = self.local(at)
        day = self.day_label(at)
        return f"{day} {local:%a %H:%M}".strip()

    def window_text(self, starts_at: dt.datetime | None, ends_at: dt.datetime | None) -> str:
        """A stage window as the humans at the event would say it: `[Day 2 Tue 14:00-16:00]`.

        Cross-day windows print both ends in full so an overnight stage reads as what it is.
        Undated events keep the original time-only form (`[14:00-16:00]`).
        """
        if starts_at is None or ends_at is None:
            return "[unscheduled]"
        start, end = self.local(starts_at), self.local(ends_at)
        if not self.dated:
            return f"[{start:%H:%M}-{end:%H:%M}]"
        start_day = self.day_label(starts_at)
        if start.date() == end.date():
            return f"[{start_day} {start:%a %H:%M}-{end:%H:%M}]"
        return f"[{start_day} {start:%a %H:%M} - {self.day_label(ends_at)} {end:%a %H:%M}]"
