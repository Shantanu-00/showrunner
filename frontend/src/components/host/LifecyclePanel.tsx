"use client";

import { useState } from "react";
import {
  Play,
  Pause,
  Archive,
  CheckCircle2,
  RotateCw,
  Camera,
  Users,
  PieChart,
  DollarSign,
  ShieldQuestion,
} from "lucide-react";
import { finalizeEvent, goLive, pauseEvent, resumeEvent, wrapEvent } from "@/lib/hostApi";
import { ApiError } from "@/lib/api";
import type { ConsoleSummary, HostEventDoc } from "@/lib/hostTypes";
import { TickCountdown } from "./TickCountdown";

const STEPS: HostEventDoc["status"][] = ["draft", "live", "paused", "wrapping", "wrapped"];

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
    <section className="mb-10 glass-card p-6 rounded-3xl border border-white/10 shadow-xl">
      <div className="flex items-center justify-between mb-4">
        <h2 className="font-[family-name:var(--font-display)] text-xl font-medium text-[var(--ivory)]">
          Event State & Telemetry
        </h2>
        <button
          type="button"
          onClick={onRefresh}
          className="flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-full bg-white/5 hover:bg-white/15 text-[var(--ink-muted)] hover:text-white transition-colors"
        >
          <RotateCw className="w-3.5 h-3.5" />
          <span>Refresh Telemetry</span>
        </button>
      </div>

      <div className="flex items-center gap-2 mb-6 flex-wrap">
        {STEPS.map((s, i) => (
          <span
            key={s}
            className={`text-xs font-mono px-3.5 py-1.5 rounded-full font-semibold capitalize transition-all ${
              i === stepIndex
                ? "bg-[var(--accent)] text-black shadow-md scale-105"
                : i < stepIndex
                ? "bg-white/10 text-[var(--ivory)] border border-white/15"
                : "bg-white/5 text-[var(--ink-muted)] border border-white/5"
            }`}
          >
            {s}
          </span>
        ))}
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-6">
        <Kpi icon={Camera} label="Ingested Photos" value={summary?.photos ?? "—"} />
        <Kpi icon={Users} label="Active Guests" value={summary?.guests ?? "—"} />
        <Kpi icon={PieChart} label="Stage Coverage" value={summary ? `${summary.coveragePct}%` : "—"} />
        <Kpi icon={DollarSign} label="Pipeline Spend" value={summary ? `$${summary.costSoFarUsd.toFixed(2)}` : "—"} />
        {/* Shown only when there is something to do. A KPI reading "0 awaiting you" every time trains
            the eye to skip the tile, which is the failure this badge exists to prevent — the held
            photos below are invisible until a host knows to scroll. */}
        {summary != null && summary.reviewCount > 0 && (
          <Kpi
            icon={ShieldQuestion}
            label="Awaiting your call"
            value={summary.reviewCount}
            tone="warn"
          />
        )}
        {(event.status === "live" || event.status === "wrapping") && (
          <TickCountdown eventId={eventId} eventClass={event.class} />
        )}
      </div>

      <div className="flex flex-wrap gap-3 pt-4 border-t border-white/10">
        {event.status === "draft" && (
          <ActionButton
            icon={Play}
            label="Go Live (Activate Director)"
            busy={busy === "go-live"}
            onClick={() => run("go-live", () => goLive(eventId))}
            primary
          />
        )}
        {event.status === "live" && (
          <>
            <ActionButton
              icon={Pause}
              label="Pause Uploads"
              busy={busy === "pause"}
              onClick={() => run("pause", () => pauseEvent(eventId))}
            />
            <ActionButton
              icon={Archive}
              label="Wrap Event"
              busy={busy === "wrap"}
              onClick={() => run("wrap", () => wrapEvent(eventId))}
            />
          </>
        )}
        {event.status === "paused" && (
          <>
            <ActionButton
              icon={Play}
              label="Resume Event"
              busy={busy === "resume"}
              onClick={() => run("resume", () => resumeEvent(eventId))}
              primary
            />
            <ActionButton
              icon={Archive}
              label="Wrap Event"
              busy={busy === "wrap"}
              onClick={() => run("wrap", () => wrapEvent(eventId))}
            />
          </>
        )}
        {event.status === "wrapping" && (
          <ActionButton
            icon={CheckCircle2}
            label="Generate Wrap Report & Finalize"
            busy={busy === "finalize"}
            onClick={() => run("finalize", () => finalizeEvent(eventId))}
            primary
          />
        )}
        {event.status === "wrapped" && (
          <p className="text-xs text-[var(--ink-muted)]">
            This event is wrapped — all media and telemetry are frozen in read-only state.
          </p>
        )}
      </div>

      {error && (
        <p className="text-xs mt-4 text-[var(--danger)] p-3 rounded-xl bg-[var(--danger)]/10 border border-[var(--danger)]/20">
          {error}
          {contactUrl && (
            <>
              {" "}
              <a href={contactUrl} className="underline font-semibold ml-1">
                Contact Developer Support
              </a>
            </>
          )}
        </p>
      )}
    </section>
  );
}

function Kpi({
  icon: Icon,
  label,
  value,
  tone = "neutral",
}: {
  icon: React.ElementType;
  label: string;
  value: string | number;
  tone?: "neutral" | "warn";
}) {
  const warn = tone === "warn";
  return (
    <div
      className={`rounded-2xl p-4 glass-card bg-black/40 border flex flex-col justify-between ${
        warn ? "border-[var(--warn)]/40" : "border-white/5"
      }`}
    >
      <div className="flex items-center justify-between mb-2">
        <span className="text-[11px] text-[var(--ink-muted)] font-medium">{label}</span>
        <Icon className={`w-4 h-4 ${warn ? "text-[var(--warn)]" : "text-[var(--gold-300)]"}`} />
      </div>
      <p
        className={`font-mono tabular-nums text-2xl font-bold ${
          warn ? "text-[var(--warn)]" : "text-[var(--ivory)]"
        }`}
      >
        {value}
      </p>
    </div>
  );
}

function ActionButton({
  icon: Icon,
  label,
  onClick,
  busy,
  primary,
}: {
  icon: React.ElementType;
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
      className={`px-5 py-3 rounded-full font-semibold text-xs flex items-center gap-2 shadow-lg transition-all ${
        primary
          ? "btn-primary"
          : "btn-secondary"
      } disabled:opacity-50`}
    >
      <Icon className="w-4 h-4 stroke-[2.2]" />
      <span>{busy ? "Processing Command…" : label}</span>
    </button>
  );
}
