"use client";

import { useState } from "react";
import { finalizeEvent, goLive, pauseEvent, resumeEvent, wrapEvent } from "@/lib/hostApi";
import { ApiError } from "@/lib/api";
import type { ConsoleSummary, HostEventDoc } from "@/lib/hostTypes";

const STEPS: HostEventDoc["status"][] = ["draft", "live", "paused", "wrapping", "wrapped"];

/** The master switch (spec 08 §2): `draft → live → paused → wrapping → wrapped`, as a stepped
 * control, plus the KPI header row (spec 12 §8). Every number here is a real aggregate from
 * `GET /console` (`backend/api/host.py::console_summary`) — refreshed on mount and after every
 * action that could have moved one, never on a timer (no client polling anywhere). */
export function LifecyclePanel({
  event,
  eventId,
  summary,
  onRefresh,
}: {
  event: HostEventDoc;
  eventId: string;
  summary: ConsoleSummary | null;
  onRefresh: () => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [contactUrl, setContactUrl] = useState<string | null>(null);

  async function run(action: string, fn: () => Promise<unknown>) {
    setBusy(action);
    setError(null);
    setContactUrl(null);
    try {
      await fn();
      onRefresh();
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message);
        if (err.contactUrl) setContactUrl(err.contactUrl);
      } else {
        setError("Something went wrong.");
      }
    } finally {
      setBusy(null);
    }
  }

  const stepIndex = STEPS.indexOf(event.status);

  return (
    <section className="mb-8">
      <div className="flex items-center justify-between mb-4">
        <p className="font-[var(--font-display)] text-lg" style={{ color: "var(--ivory)" }}>
          Controls
        </p>
        <button
          type="button"
          onClick={onRefresh}
          className="text-xs px-3 py-1 rounded-[var(--radius-pill)]"
          style={{ border: "var(--hairline)", color: "var(--ink-muted)" }}
        >
          ↻ Refresh
        </button>
      </div>

      <div className="flex items-center gap-2 mb-6 flex-wrap">
        {STEPS.map((s, i) => (
          <span
            key={s}
            className="text-xs font-mono px-3 py-1.5 rounded-[var(--radius-pill)]"
            style={{
              background: i === stepIndex ? "var(--accent)" : "var(--bg-1)",
              color: i === stepIndex ? "var(--bg-0)" : "var(--ink-muted)",
              border: i === stepIndex ? "none" : "var(--hairline)",
            }}
          >
            {s}
          </span>
        ))}
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-6">
        <Kpi label="Photos" value={summary?.photos ?? "—"} />
        <Kpi label="Guests" value={summary?.guests ?? "—"} />
        <Kpi label="Coverage" value={summary ? `${summary.coveragePct}%` : "—"} />
        <Kpi label="Cost so far" value={summary ? `$${summary.costSoFarUsd.toFixed(2)}` : "—"} />
      </div>

      <div className="flex flex-wrap gap-3">
        {event.status === "draft" && (
          <ActionButton label="Go Live" busy={busy === "go-live"} onClick={() => run("go-live", () => goLive(eventId))} primary />
        )}
        {event.status === "live" && (
          <>
            <ActionButton label="Pause uploads" busy={busy === "pause"} onClick={() => run("pause", () => pauseEvent(eventId))} />
            <ActionButton label="Wrap event" busy={busy === "wrap"} onClick={() => run("wrap", () => wrapEvent(eventId))} />
          </>
        )}
        {event.status === "paused" && (
          <>
            <ActionButton label="Resume" busy={busy === "resume"} onClick={() => run("resume", () => resumeEvent(eventId))} primary />
            <ActionButton label="Wrap event" busy={busy === "wrap"} onClick={() => run("wrap", () => wrapEvent(eventId))} />
          </>
        )}
        {event.status === "wrapping" && (
          <ActionButton
            label="Generate wrap report & finish"
            busy={busy === "finalize"}
            onClick={() => run("finalize", () => finalizeEvent(eventId))}
            primary
          />
        )}
        {event.status === "wrapped" && (
          <p className="text-sm" style={{ color: "var(--ink-muted)" }}>
            This event is wrapped — read-only.
          </p>
        )}
      </div>

      {error && (
        <p className="text-sm mt-4" style={{ color: "var(--danger)" }}>
          {error}
          {contactUrl && (
            <>
              {" "}
              <a href={contactUrl} className="underline">
                Contact the developer
              </a>
            </>
          )}
        </p>
      )}
    </section>
  );
}

function Kpi({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-[var(--radius-card)] p-3" style={{ background: "var(--bg-1)", border: "var(--hairline)" }}>
      <p className="text-xs mb-1" style={{ color: "var(--ink-muted)" }}>
        {label}
      </p>
      <p className="font-mono tabular-nums text-xl" style={{ color: "var(--ivory)" }}>
        {value}
      </p>
    </div>
  );
}

function ActionButton({
  label,
  onClick,
  busy,
  primary,
}: {
  label: string;
  onClick: () => void;
  busy: boolean;
  primary?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={busy}
      className="px-5 py-2.5 rounded-[var(--radius-pill)] font-medium text-sm"
      style={
        primary
          ? { background: "var(--accent)", color: "var(--bg-0)", opacity: busy ? 0.6 : 1 }
          : { border: "var(--hairline)", color: "var(--ivory)", opacity: busy ? 0.6 : 1 }
      }
    >
      {busy ? "Working…" : label}
    </button>
  );
}
