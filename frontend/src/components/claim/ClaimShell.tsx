"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ensureAnonymousAuth, refreshClaims } from "@/lib/firebase";
import { claimByCode, ApiError } from "@/lib/api";

/** `/events/{eventId}/claim#<code>` (spec 02 §3.1, matches the real URL
 * `backend/api/identity.py::create_claim_link` builds). No custom token is minted — the server
 * grants `personId` directly to whichever uid is on this request's bearer token, so the only
 * client-side step after a successful call is a force-refreshed ID token. */
export function ClaimShell({ eventId }: { eventId: string }) {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const code = window.location.hash.slice(1);
    if (!code) {
      setError("This link is missing its code.");
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
            ? "This link has expired or was revoked."
            : "Couldn't reach the director yet — try again in a moment."
        );
      });
  }, [eventId, router]);

  return (
    <div
      className="fixed inset-0 flex flex-col items-center justify-center px-6 text-center"
      style={{ background: "var(--bg-0)" }}
    >
      {error ? (
        <>
          <p className="text-5xl mb-4" aria-hidden>
            🔗
          </p>
          <p style={{ color: "var(--ink-muted)" }}>{error}</p>
        </>
      ) : (
        <>
          <div className="w-16 h-16 rounded-full skeleton-shimmer mb-6" />
          <p style={{ color: "var(--ink-muted)" }}>Opening your album…</p>
        </>
      )}
    </div>
  );
}
