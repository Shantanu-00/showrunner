"use client";

import { useEffect, useState } from "react";
import { BellRing, BellOff, Share, Plus, ShieldOff } from "lucide-react";
import {
  disablePush,
  enablePush,
  isInstalledPwa,
  pushPermission,
  pushSupport,
  syncPush,
  type PushSupport,
} from "@/lib/push";
import { useHaptics } from "@/lib/useHaptics";

/** "Tell me what to photograph" — the guest's opt-in to Web Push (`lib/push.ts`).
 *
 * Deliberately a row in the Me tab rather than an interstitial on load. A permission prompt fired at
 * a guest who has not yet understood what the event *is* gets dismissed, and `Notification.permission
 * === "denied"` cannot be undone from JavaScript — one badly-timed dialog costs that phone push for
 * the whole event. So the browser is only ever asked from the tap on this row, after copy that says
 * what the notification will be about.
 *
 * Each unavailable state gets its own honest sentence instead of a disabled button, because they are
 * genuinely different situations with different (or no) ways forward. The one worth the extra card is
 * iOS: Safari has had Web Push since 16.4 but grants it **only to an installed PWA**, so an iPhone in
 * a normal tab is not "unsupported" — it is two taps away, and those two taps are worth drawing.
 */
export function PushOptIn({ eventId }: { eventId: string }) {
  const [support, setSupport] = useState<PushSupport | null>(null);
  const [granted, setGranted] = useState(false);
  const [busy, setBusy] = useState(false);
  const { tapHaptic } = useHaptics();

  useEffect(() => {
    setSupport(pushSupport());
    setGranted(pushPermission() === "granted");
    // Refresh the stored token whenever this surface mounts and permission is already granted. FCM
    // rotates tokens, and a five-day event outlives one: without this the guest silently stops being
    // reachable somewhere around day two, with nothing anywhere reporting it.
    void syncPush(eventId);
  }, [eventId]);

  async function toggle() {
    tapHaptic();
    setBusy(true);
    try {
      if (granted) {
        await disablePush(eventId);
        setGranted(false);
      } else {
        const result = await enablePush(eventId);
        setSupport(result);
        setGranted(result.ok);
      }
    } finally {
      setBusy(false);
    }
  }

  if (!support) return null;

  // iOS in a browser tab: the capability is real, it is just gated behind installing the app. Showing
  // the actual gesture ("Share → Add to Home Screen") is the difference between a guest doing it and
  // a guest concluding the feature is broken.
  if (!support.ok && support.reason === "needs-install" && !isInstalledPwa()) {
    return (
      <div className="rounded-2xl glass-card p-4 border border-white/10">
        <div className="flex items-center gap-3">
          <div className="p-2 rounded-xl bg-[var(--gold-500)]/15 text-[var(--accent)]">
            <BellRing className="w-5 h-5" />
          </div>
          <div className="flex-1">
            <h4 className="text-sm font-semibold text-[var(--ivory)]">
              Get the director&rsquo;s photo requests
            </h4>
            <p className="text-xs text-[var(--ink-muted)] leading-relaxed">
              On iPhone, alerts need the app on your home screen first — Apple only allows them there.
            </p>
          </div>
        </div>
        <div className="mt-3 pt-3 border-t border-white/10 flex items-center gap-2 text-xs text-[var(--ink-muted)]">
          <span className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg bg-white/5 border border-white/10 text-[var(--ivory)] font-medium">
            <Share className="w-3.5 h-3.5" />
            Share
          </span>
          <span aria-hidden>→</span>
          <span className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg bg-white/5 border border-white/10 text-[var(--ivory)] font-medium">
            <Plus className="w-3.5 h-3.5" />
            Add to Home Screen
          </span>
        </div>
      </div>
    );
  }

  if (!support.ok && support.reason === "blocked") {
    return (
      <div className="rounded-2xl glass-card p-4 border border-white/10 flex items-center gap-3">
        <div className="p-2 rounded-xl bg-white/5 text-[var(--ink-muted)]">
          <ShieldOff className="w-5 h-5" />
        </div>
        <div className="flex-1">
          <h4 className="text-sm font-semibold text-[var(--ivory)]">Alerts are switched off</h4>
          <p className="text-xs text-[var(--ink-muted)] leading-relaxed">
            You blocked notifications for this site, and only your browser settings can undo that. The
            missions still show up in the app whenever you open it.
          </p>
        </div>
      </div>
    );
  }

  // "not-configured" (no VAPID key in this build) and "unsupported" both render nothing. A guest can
  // do nothing about either, and a row explaining our deploy configuration to them would be noise.
  if (!support.ok) return null;

  return (
    <button
      type="button"
      onClick={() => void toggle()}
      disabled={busy}
      className="w-full flex items-center gap-3 p-4 rounded-2xl glass-card border border-white/10 text-left hover:border-[var(--accent)] transition-all disabled:opacity-50"
    >
      <div
        className={`p-2 rounded-xl ${
          granted
            ? "bg-[var(--gold-500)]/15 text-[var(--accent)]"
            : "bg-white/5 text-[var(--ink-muted)]"
        }`}
      >
        {granted ? <BellRing className="w-5 h-5" /> : <BellOff className="w-5 h-5" />}
      </div>
      <div className="flex-1">
        <h4 className="text-sm font-semibold text-[var(--ivory)]">
          {granted ? "Photo requests are on" : "Get the director's photo requests"}
        </h4>
        <p className="text-xs text-[var(--ink-muted)] leading-relaxed">
          {busy
            ? "Just a moment…"
            : granted
              ? "You'll be buzzed when the director spots a shot that's missing. Tap to turn off."
              : "A buzz when a moment is going uncovered — nothing else, ever."}
        </p>
      </div>
    </button>
  );
}
