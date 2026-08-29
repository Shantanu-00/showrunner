"use client";

import { useCallback, useEffect, useState } from "react";
import { ensureAnonymousAuth, refreshClaims } from "@/lib/firebase";
import { redeemHostCode, getConsoleSummary } from "@/lib/hostApi";
import { listenHostEvent } from "@/lib/hostFirestore";
import { useRouteEventId } from "@/lib/routeParams";
import type { ConsoleSummary, HostEventDoc } from "@/lib/hostTypes";
import { FreezeButton } from "./FreezeButton";
import { LifecyclePanel } from "./LifecyclePanel";
import { ItineraryPanel } from "./ItineraryPanel";
import { StageOverridePanel } from "./StageOverridePanel";
import { WrapReportPanel } from "./WrapReportPanel";

type AuthState = "checking" | "need-code" | "ready" | "not-found";

/** `/host/{eventId}` — the producer's booth (spec 08 §4, spec 12 §8), descoped this session to
 * four things plus the persistent panic controls: timeline (paste→parse→review), the master
 * switch, "Now: ▶ stage", and the wrap-up report. Cut: the coverage heat-grid, review-queue UI
 * (`POST /media/{id}/review` exists — curl it), the People tab, director prefs. */
export function HostConsoleShell({ eventId: fallbackEventId }: { eventId: string }) {
  const eventId = useRouteEventId("/host/", fallbackEventId);
  const [authState, setAuthState] = useState<AuthState>("checking");
  const [codeInput, setCodeInput] = useState("");
  const [codeError, setCodeError] = useState<string | null>(null);
  const [redeeming, setRedeeming] = useState(false);
  const [event, setEvent] = useState<HostEventDoc | null>(null);
  const [summary, setSummary] = useState<ConsoleSummary | null>(null);

  const refreshSummary = useCallback(() => {
    void getConsoleSummary(eventId).then(setSummary, () => {});
  }, [eventId]);

  const checkAccess = useCallback(async () => {
    await ensureAnonymousAuth();
    const params = new URLSearchParams(window.location.search);
    const hostCode = params.get("hostCode");
    if (hostCode) {
      try {
        await redeemHostCode(hostCode);
      } catch {
        // Falls through to the claim check below — an expired/bad code just means no access yet.
      }
    }
    const claims = await refreshClaims();
    setAuthState(claims.host === eventId ? "ready" : "need-code");
  }, [eventId]);

  useEffect(() => {
    void checkAccess();
  }, [checkAccess]);

  useEffect(() => {
    if (authState !== "ready") return;
    const unsub = listenHostEvent(
      eventId,
      (doc) => setEvent(doc ?? null),
      () => setAuthState("not-found")
    );
    refreshSummary();
    return unsub;
  }, [authState, eventId, refreshSummary]);

  async function submitCode() {
    if (!codeInput.trim()) return;
    setRedeeming(true);
    setCodeError(null);
    try {
      await redeemHostCode(codeInput.trim());
      const claims = await refreshClaims();
      setAuthState(claims.host === eventId ? "ready" : "need-code");
      if (claims.host !== eventId) setCodeError("That code isn't for this event.");
    } catch {
      setCodeError("That code didn't work — check it and try again.");
    } finally {
      setRedeeming(false);
    }
  }

  if (authState === "checking") {
    return <p className="text-center mt-24" style={{ color: "var(--ink-muted)" }}>Checking access…</p>;
  }

  if (authState === "not-found") {
    return <p className="text-center mt-24" style={{ color: "var(--ink-muted)" }}>Unknown event.</p>;
  }

  if (authState === "need-code") {
    return (
      <div className="max-w-md mx-auto px-5 py-24 text-center">
        <p className="font-[family-name:var(--font-display)] text-xl mb-3" style={{ color: "var(--ivory)" }}>
          Host access needed
        </p>
        <p className="text-sm mb-6" style={{ color: "var(--ink-muted)" }}>
          Paste your host link&rsquo;s code or your recovery code.
        </p>
        <input
          value={codeInput}
          onChange={(e) => setCodeInput(e.target.value)}
          placeholder="Host code"
          className="w-full mb-3 px-4 py-3 rounded-[var(--radius-card)]"
          style={{ background: "var(--bg-1)", border: "var(--hairline)", color: "var(--ivory)" }}
        />
        {codeError && (
          <p className="text-sm mb-3" style={{ color: "var(--danger)" }}>
            {codeError}
          </p>
        )}
        <button
          type="button"
          onClick={() => void submitCode()}
          disabled={redeeming}
          className="w-full py-3 rounded-[var(--radius-pill)] font-medium"
          style={{ background: "var(--accent)", color: "var(--bg-0)", opacity: redeeming ? 0.6 : 1 }}
        >
          {redeeming ? "Checking…" : "Unlock console"}
        </button>
      </div>
    );
  }

  if (!event) {
    return <p className="text-center mt-24" style={{ color: "var(--ink-muted)" }}>Loading…</p>;
  }

  return (
    <div className="max-w-2xl mx-auto px-5 pb-24 pt-6">
      <div
        className="sticky top-0 z-40 -mx-5 px-5 py-3 mb-8 flex items-center justify-between gap-3"
        style={{ background: "var(--bg-0)", borderBottom: "var(--hairline)" }}
      >
        <div className="min-w-0">
          <p className="font-[family-name:var(--font-display)] text-lg truncate" style={{ color: "var(--ivory)" }}>
            {event.name}
          </p>
          <p className="text-xs font-mono" style={{ color: "var(--ink-muted)" }}>
            {event.status}
          </p>
        </div>
        <FreezeButton
          eventId={eventId}
          frozen={event.publicFrozen}
          onChanged={(frozen) => setEvent((prev) => (prev ? { ...prev, publicFrozen: frozen } : prev))}
        />
      </div>

      <LifecyclePanel event={event} eventId={eventId} summary={summary} onRefresh={refreshSummary} />
      <ItineraryPanel event={event} eventId={eventId} />
      <StageOverridePanel event={event} eventId={eventId} onChanged={refreshSummary} />
      {event.wrapReport && <WrapReportPanel report={event.wrapReport} />}
    </div>
  );
}
