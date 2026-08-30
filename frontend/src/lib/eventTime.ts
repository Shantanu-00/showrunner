// Small, dependency-free day math mirroring the backend's (spec: `GET /v1/events/{id}/public`'s
// `startsOn`/`endsOn` + per-stage `day`). The backend computes `day` server-side already — these
// helpers exist so any client surface that wants "Day N" copy formats it the same way, whether it
// has the backend-computed index (`dayLabelFromIndex`) or only a raw instant to place on the
// timeline (`dayIndex`/`dayLabel`).

/** Formats `at`'s local calendar date in `timezone` as `YYYY-MM-DD` (no external tz library). */
function localYmd(at: Date, timezone: string | undefined): string {
  const fmt = new Intl.DateTimeFormat("en-CA", {
    timeZone: timezone || undefined,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });
  return fmt.format(at);
}

function ymdToUtcMs(ymd: string): number | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(ymd);
  if (!m) return null;
  return Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
}

/**
 * 1-based day index of `at`, computed in the event's local timezone, relative to `startsOn`
 * ("YYYY-MM-DD", already a local date in that timezone). Returns `null` for undated events or
 * unparseable input — never throws.
 */
export function dayIndex(
  startsOn: string | null | undefined,
  timezone: string | undefined,
  at: Date
): number | null {
  if (!startsOn) return null;
  const startMs = ymdToUtcMs(startsOn);
  if (startMs === null) return null;
  const atMs = ymdToUtcMs(localYmd(at, timezone));
  if (atMs === null) return null;
  const diffDays = Math.round((atMs - startMs) / 86_400_000);
  return diffDays + 1;
}

/** "Day 3" for a dated event at `at`, or "" for an undated one — never a negative/zero day. */
export function dayLabel(
  startsOn: string | null | undefined,
  timezone: string | undefined,
  at: Date
): string {
  const idx = dayIndex(startsOn, timezone, at);
  return idx !== null && idx > 0 ? `Day ${idx}` : "";
}

/** Same "Day N" formatting, from a day index the backend already computed (e.g. a stage's own
 * `day` field) — the common case for gallery chips and the kiosk header, which never need to
 * redo the timezone math themselves. */
export function dayLabelFromIndex(day: number | null | undefined): string {
  return typeof day === "number" && Number.isFinite(day) && day > 0 ? `Day ${day}` : "";
}
