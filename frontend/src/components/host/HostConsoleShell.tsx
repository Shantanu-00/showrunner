"use client";

import { useCallback, useEffect, useState } from "react";
import { ShieldCheck, KeyRound, ArrowRight, LayoutDashboard, Calendar, RefreshCw, Camera, Tv } from "lucide-react";
import { ensureAnonymousAuth, refreshClaims, type Claims } from "@/lib/firebase";
import { redeemHostCode, getConsoleSummary } from "@/lib/hostApi";
import { listenHostEvent } from "@/lib/hostFirestore";
import { useRouteEventId } from "@/lib/routeParams";
import type { ConsoleSummary, HostEventDoc } from "@/lib/hostTypes";
import { FreezeButton } from "./FreezeButton";
import { AccessPanel } from "./AccessPanel";
import { ClaimReviewPanel } from "./ClaimReviewPanel";
import { ReviewPanel } from "./ReviewPanel";
import { rememberEvent } from "./rememberedEvents";
import { LifecyclePanel } from "./LifecyclePanel";
import { ItineraryPanel } from "./ItineraryPanel";
import { StageOverridePanel } from "./StageOverridePanel";
import { WrapReportPanel } from "./WrapReportPanel";
import { SettingsPanel } from "./SettingsPanel";
import { PeoplePanel } from "./PeoplePanel";

type AuthState = "checking" | "need-code" | "ready" | "not-found";

/** The `host` custom claim is moving from a single event id to a list, so one browser can hold
 * several events at once. Read both shapes: check the array first, fallback to legacy scalar. */
function grantsHostOf(claims: Claims, eventId: string): boolean {
  if (claims.hosts?.includes(eventId)) return true;
  if (claims.host === eventId) return true;
  return false;
}

export function HostConsoleShell({ eventId: fallbackEventId }: { eventId: string }) {
  const eventId = useRouteEventId("/host/", fallbackEventId);
  const [authState, setAuthState] = useState<AuthState>("checking");
  const [codeInput, setCodeInput] = useState("");
  const [codeError, setCodeError] = useState<string | null>(null);
  const [otherEvent, setOtherEvent] = useState<{ eventId: string; name: string } | null>(null);
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
        // Fall through
      } finally {
        // The code is spent either way, and leaving it in the address bar leaves it in browser
        // history, in a screenshot, and in whatever the host pastes to a co-host next. Strip it
        // without a navigation so nothing re-mounts and no history entry is added.
        params.delete("hostCode");
        const query = params.toString();
        window.history.replaceState(
          null,
          "",
          `${window.location.pathname}${query ? `?${query}` : ""}${window.location.hash}`
        );
      }
    }
    // `_grant_host` writes the custom claim synchronously before create/redeem ever responds, but
    // that write and this forced token refresh hit different Firebase Identity Platform endpoints —
    // immediately after creating (or redeeming a link), the refresh can occasionally still race the
    // claim's propagation. A brief retry absorbs that instead of bouncing a brand-new host into the
    // code prompt for an event they were just handed.
    for (let attempt = 0; attempt < 4; attempt++) {
      const claims = await refreshClaims();
      if (grantsHostOf(claims, eventId)) {
        setAuthState("ready");
        return;
      }
      if (attempt < 3) await new Promise((r) => setTimeout(r, 400));
    }
    setAuthState("need-code");
  }, [eventId]);

  useEffect(() => {
    void checkAccess();
  }, [checkAccess]);

  useEffect(() => {
    if (authState !== "ready") return;
    const unsub = listenHostEvent(
      eventId,
      (doc) => {
        setEvent(doc ?? null);
        // Whichever way this host got in — creation, a recovery code, a co-host link — this browser
        // can now offer them "continue to your event" from /host instead of asking for the id again.
        if (doc?.name) rememberEvent(eventId, doc.name);
      },
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
      const redeemed = await redeemHostCode(codeInput.trim());
      const claims = await refreshClaims();
      const ok = grantsHostOf(claims, eventId);
      setAuthState(ok ? "ready" : "need-code");
      if (!ok) {
        // The code was valid — it just belongs to a different event. Say which one and offer the
        // door, rather than "that code isn't for this event" and a dead end.
        setCodeError(null);
        setOtherEvent(
          redeemed.eventId
            ? { eventId: redeemed.eventId, name: redeemed.eventName ?? redeemed.eventId }
            : null
        );
        if (!redeemed.eventId) setCodeError("That code isn't for this event.");
      }
    } catch {
      setCodeError("That code didn't work — please check and try again.");
    } finally {
      setRedeeming(false);
    }
  }

  if (authState === "checking") {
    return (
      // Spec 12 §4's no-spinner rule: a branded shimmer plus copy that names what is happening,
      // never an indeterminate ring.
      <div className="max-w-3xl mx-auto px-5 pt-12">
        <div className="h-9 w-56 rounded-xl skeleton-shimmer bg-white/5 mb-6" />
        <div className="h-32 rounded-3xl skeleton-shimmer bg-white/5 mb-3" />
        <div className="h-32 rounded-3xl skeleton-shimmer bg-white/5 mb-4" />
        <p className="text-xs text-[var(--ink-muted)]">Checking you&rsquo;re the host of this event…</p>
      </div>
    );
  }

  if (authState === "not-found") {
    return (
      <div className="text-center mt-24 px-6 py-12 rounded-2xl glass-card max-w-md mx-auto">
        <p className="text-sm text-[var(--ink-muted)]">Unknown or expunged event ID.</p>
      </div>
    );
  }

  if (authState === "need-code") {
    return (
      <div className="max-w-md mx-auto px-5 py-24 text-center animate-fadeIn">
        <div className="p-8 rounded-3xl glass-card border border-white/10 shadow-2xl flex flex-col items-center">
          <div className="w-14 h-14 rounded-full bg-[var(--gold-500)]/15 text-[var(--accent)] flex items-center justify-center mb-4">
            <KeyRound className="w-7 h-7" />
          </div>
          <h2 className="font-[family-name:var(--font-display)] text-2xl font-semibold text-[var(--ivory)] mb-2">
            Host Access Required
          </h2>
          <p className="text-xs text-[var(--ink-muted)] mb-6 max-w-xs leading-relaxed">
            Enter your host invite code or recovery key to open the event control booth.
          </p>

          <input
            value={codeInput}
            onChange={(e) => setCodeInput(e.target.value)}
            placeholder="Enter host code"
            className="w-full px-4 py-3 rounded-xl bg-black/50 border border-white/10 text-sm font-mono text-center text-[var(--ivory)] placeholder:text-[var(--ink-faint)] focus:border-[var(--accent)] focus:outline-none mb-3"
          />

          {codeError && (
            <p className="text-xs text-[var(--danger)] mb-4">{codeError}</p>
          )}

          {otherEvent && (
            <div className="w-full mb-4 p-4 rounded-2xl bg-[var(--warn)]/10 border border-[var(--warn)]/30 text-left">
              <p className="text-xs text-[var(--ivory-dim)] leading-relaxed mb-3">
                That code opens <strong className="text-[var(--ivory)]">{otherEvent.name}</strong>,
                not this event.
              </p>
              <a
                href={`/host/${encodeURIComponent(otherEvent.eventId)}`}
                className="btn-secondary inline-flex items-center gap-1.5 px-4 py-2 text-xs font-semibold"
              >
                <span>Go to that event</span>
                <ArrowRight className="w-3.5 h-3.5 stroke-[2.5]" />
              </a>
            </div>
          )}

          <button
            type="button"
            disabled={redeeming || !codeInput.trim()}
            onClick={() => void submitCode()}
            className="btn-primary w-full py-3.5 rounded-full text-sm font-semibold flex items-center justify-center gap-2 disabled:opacity-40"
          >
            <span>{redeeming ? "Verifying…" : "Authenticate as Host"}</span>
            <ArrowRight className="w-4 h-4 stroke-[2.5]" />
          </button>

          <a
            href="/host"
            className="mt-4 text-[11px] font-semibold text-[var(--ink-muted)] hover:text-[var(--accent)] transition-colors"
          >
            Don&rsquo;t have a code? Create an event instead
          </a>
        </div>
      </div>
    );
  }

  if (!event) return null;

  return (
    <div className="min-h-screen pb-24 max-w-3xl mx-auto px-5 pt-8">
      {/* Header Command Bar */}
      <header className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pb-6 mb-8 border-b border-white/10">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <span className="text-[11px] font-mono uppercase tracking-[0.2em] text-[var(--accent)]">
              HOST DIRECTORY BOOTH
            </span>
            <span className="text-xs px-2 py-0.5 rounded-full bg-white/5 border border-white/10 text-[var(--ink-muted)] font-mono">
              {event.class}
            </span>
          </div>
          <h1 className="font-[family-name:var(--font-display)] text-3xl font-semibold text-gold-gradient">
            {event.name}
          </h1>
        </div>

        <div className="flex flex-wrap items-center gap-2 sm:gap-3">
          {/* The two surfaces a host could not reach from their own console.
           *
           * A host is at the event too — they are usually the one taking the most photographs — and
           * this screen offered no camera and no gallery, so the only way in was to find the guest
           * link they had shared with everybody else. `POST /join` is idempotent and a host is
           * already a member, so this is just the door they were standing next to.
           *
           * And the wall: a kiosk had no entry point on any screen in the product. Both open in a new
           * tab, because both are somewhere the host comes back from. */}
          <a
            href={`/join/${encodeURIComponent(eventId)}`}
            target="_blank"
            rel="noreferrer"
            className="btn-secondary inline-flex items-center gap-1.5 px-3.5 py-2 text-xs font-semibold"
            title="Open this event as a guest — camera, gallery and your own album"
          >
            <Camera className="w-3.5 h-3.5" />
            <span>Join &amp; shoot</span>
          </a>
          <a
            href={`/kiosk/${encodeURIComponent(eventId)}`}
            target="_blank"
            rel="noreferrer"
            className="btn-secondary inline-flex items-center gap-1.5 px-3.5 py-2 text-xs font-semibold"
            title="Put the show on a screen"
          >
            <Tv className="w-3.5 h-3.5" />
            <span>Open the wall</span>
          </a>
          <FreezeButton
            eventId={eventId}
            frozen={event.publicFrozen ?? false}
            onChanged={(frozen) => setEvent({ ...event, publicFrozen: frozen })}
          />
        </div>
      </header>

      <LifecyclePanel
        event={event}
        eventId={eventId}
        summary={summary}
        onRefresh={() => {
          refreshSummary();
        }}
      />

      {/* Highest in the column after the lifecycle KPIs on purpose: a held claim is a guest standing
          in the room who cannot see their own photos, and it is only resolvable here. */}
      <ClaimReviewPanel eventId={eventId} />

      {/* The other half of that same trust boundary: adding someone here never opens their album by
          itself (only the panel above does that), but it is where coverage tracking and tiers live. */}
      <PeoplePanel eventId={eventId} />

      {/* Directly beneath it, and for the same reason: every conservative default in the perception
          pipeline parks a photo here, and until this panel existed none of them could be cleared.
          `onDecided` re-reads the summary because the KPI badge is computed from the same predicate
          this panel lists by — they must never disagree. */}
      <ReviewPanel eventId={eventId} onDecided={refreshSummary} />

      <AccessPanel event={event} eventId={eventId} guestCount={summary?.guests ?? null} />

      <StageOverridePanel
        event={event}
        eventId={eventId}
        onChanged={() => {
          refreshSummary();
        }}
      />

      <ItineraryPanel event={event} eventId={eventId} />

      <SettingsPanel event={event} eventId={eventId} />

      {event.wrapReport && <WrapReportPanel report={event.wrapReport} eventId={eventId} />}
    </div>
  );
}
