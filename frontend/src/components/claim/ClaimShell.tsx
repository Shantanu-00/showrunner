"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Link2, Sparkles, AlertTriangle } from "lucide-react";
import { ensureAnonymousAuth, refreshClaims } from "@/lib/firebase";
import { claimByCode, ApiError } from "@/lib/api";
import { useRouteEventId } from "@/lib/routeParams";

export function ClaimShell({ eventId: fallbackEventId }: { eventId: string }) {
  const eventId = useRouteEventId("/events/", fallbackEventId);
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const code = window.location.hash.slice(1);
    if (!code) {
      setError("This access link is missing its authorization code.");
      return;
    }
    void ensureAnonymousAuth()
      .then(() => claimByCode(code))
      .then(async () => {
        await refreshClaims();
        router.replace(`/join/${eventId}?tab=me`);
      })
      .catch((err) => {
        setError(
          err instanceof ApiError && (err.status === 403 || err.status === 404)
            ? "This magic link has expired or was revoked by the host."
            : "Connection error reaching host authority — please retry."
        );
      });
  }, [eventId, router]);

  return (
    <div className="fixed inset-0 flex flex-col items-center justify-center px-6 text-center bg-[var(--bg-0)] animate-fadeIn">
      {error ? (
        <div className="max-w-sm p-8 rounded-3xl glass-card border border-[var(--danger)]/30 flex flex-col items-center">
          <div className="w-14 h-14 rounded-full bg-[var(--danger)]/20 text-[var(--danger)] flex items-center justify-center mb-4">
            <AlertTriangle className="w-7 h-7" />
          </div>
          <h3 className="font-[family-name:var(--font-display)] text-xl font-medium text-[var(--ivory)] mb-2">
            Authorization Failed
          </h3>
          <p className="text-xs text-[var(--ink-muted)] mb-6 leading-relaxed">{error}</p>
          <a
            href={`/join/${eventId}`}
            className="btn-primary w-full py-3 text-xs font-semibold text-center"
          >
            Return to Event
          </a>
        </div>
      ) : (
        <div className="flex flex-col items-center">
          <div className="relative w-16 h-16 rounded-full skeleton-shimmer mb-4 flex items-center justify-center border border-[var(--hairline)]">
            <Sparkles className="w-8 h-8 text-[var(--accent)] animate-pulse" />
          </div>
          <h3 className="font-[family-name:var(--font-display)] text-xl font-medium text-[var(--ivory)] mb-1">
            Redeeming Magic Link
          </h3>
          <p className="text-xs text-[var(--ink-muted)] font-mono">
            Verifying token claims & opening private album…
          </p>
        </div>
      )}
    </div>
  );
}
