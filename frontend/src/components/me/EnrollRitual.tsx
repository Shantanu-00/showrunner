"use client";

import { useRef, useState } from "react";
import { ApiError, enrollPerson } from "@/lib/api";
import { refreshClaims } from "@/lib/firebase";
import type { EnrollOutcome } from "@/lib/types";

type Phase = "consent" | "enrolling" | "linked" | "held_for_review" | "pending_host_approval" | "error";

/** Consent moment C2 (spec 02 §4) — the full-screen biometric ritual. Live-camera-only capture
 * (spec 02 §3's anti-abuse rule: no gallery picker), an explicit un-pre-ticked checkbox, and the
 * retention sentence stated in full — this is the screenshot-worthy consent frame, not
 * boilerplate. Mirrors `backend/api/identity.py::enroll`'s three real outcomes exactly. */
export function EnrollRitual({
  eventId,
  onEnrolled,
  onCancel,
}: {
  eventId: string;
  /** Fires only for `linked`/`held_for_review` — both cases actually create a person and grant
   * the caller's uid its `personId` claim. `pending_host_approval` grants nothing. */
  onEnrolled: (personId: string, outcome: EnrollOutcome) => void;
  onCancel: () => void;
}) {
  const [agreed, setAgreed] = useState(false);
  const [phase, setPhase] = useState<Phase>("consent");
  const [message, setMessage] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  function fileToBase64(file: File): Promise<string> {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve((reader.result as string).split(",")[1] ?? "");
      reader.onerror = reject;
      reader.readAsDataURL(file);
    });
  }

  async function onSelfieSelected(file: File | undefined) {
    if (!file) return;
    setPhase("enrolling");
    try {
      const selfie = await fileToBase64(file);
      const res = await enrollPerson(eventId, {
        selfie,
        biometricConsent: true,
        retentionNoticeShown: true,
      });
      setMessage(res.message);
      if (res.outcome === "pending_host_approval") {
        setPhase("pending_host_approval");
        return;
      }
      await refreshClaims();
      onEnrolled(res.personId!, res.outcome);
      setPhase(res.outcome);
    } catch (err) {
      setMessage(
        err instanceof ApiError
          ? "Couldn't reach the director yet — try again in a moment."
          : "Something went wrong — try again."
      );
      setPhase("error");
    }
  }

  const doneScreen = (icon: string, title: string) => (
    <>
      <p className="text-5xl mb-4" aria-hidden>
        {icon}
      </p>
      <h2 className="font-[var(--font-display)] text-2xl mb-3" style={{ color: "var(--ivory)" }}>
        {title}
      </h2>
      {message && (
        <p className="text-sm max-w-xs" style={{ color: "var(--ink-muted)" }}>
          {message}
        </p>
      )}
      <button
        type="button"
        onClick={onCancel}
        className="mt-8 py-3 px-8 rounded-[var(--radius-pill)] font-medium"
        style={{ background: "var(--accent)", color: "var(--bg-0)" }}
      >
        Done
      </button>
    </>
  );

  return (
    <div
      className="fixed inset-0 z-50 flex flex-col items-center justify-center px-6 text-center"
      style={{ background: "var(--bg-0)" }}
    >
      <button
        type="button"
        onClick={onCancel}
        className="absolute top-6 right-6 text-2xl"
        style={{ color: "var(--ink-muted)" }}
        aria-label="Close"
      >
        ×
      </button>

      {phase === "linked"
        ? doneScreen("✨", "Your album is ready")
        : phase === "held_for_review"
          ? doneScreen("🕊️", "The host is confirming it's you")
          : phase === "pending_host_approval"
            ? doneScreen("🕊️", "Waiting on the host")
            : phase === "error"
              ? doneScreen("⚠️", "Something went wrong")
              : phase === "enrolling"
                ? (
                    <>
                      <div
                        className="w-40 h-40 rounded-full mb-6 skeleton-shimmer"
                        style={{ border: "var(--hairline)" }}
                      />
                      <p className="text-sm" style={{ color: "var(--ink-muted)" }}>
                        Looking for you in the archives…
                      </p>
                    </>
                  )
                : (
                    <>
                      <div className="relative w-40 h-40 mb-6">
                        <div
                          className="absolute inset-0 rounded-full consent-ring"
                          style={{
                            background: `conic-gradient(var(--accent) 0deg 90deg, transparent 90deg 360deg)`,
                            mask: "radial-gradient(farthest-side, transparent calc(100% - 3px), #000 calc(100% - 3px))",
                          }}
                          aria-hidden
                        />
                        <div
                          className="absolute inset-2 rounded-full flex items-center justify-center text-4xl"
                          style={{ background: "var(--bg-1)" }}
                          aria-hidden
                        >
                          🤳
                        </div>
                      </div>

                      <h2 className="font-[var(--font-display)] text-2xl mb-3" style={{ color: "var(--ivory)" }}>
                        Unlock your album
                      </h2>
                      <p className="text-sm max-w-xs mb-6" style={{ color: "var(--ink-muted)" }}>
                        Your selfie finds every photo of you at this event. It never leaves this
                        event, and your face data stays inside this event and is deleted with it.
                      </p>

                      <label
                        className="flex items-start gap-3 text-left max-w-xs mb-6 text-sm"
                        style={{ color: "var(--ivory)" }}
                      >
                        <input
                          type="checkbox"
                          checked={agreed}
                          onChange={(e) => setAgreed(e.target.checked)}
                          className="mt-1 w-5 h-5"
                        />
                        <span>
                          I agree to use my selfie to find photos of me. It&rsquo;s kept for this
                          event plus 30 days. You can delete it anytime in Settings.
                        </span>
                      </label>

                      <input
                        ref={inputRef}
                        type="file"
                        accept="image/*"
                        capture="user"
                        className="hidden"
                        onChange={(e) => void onSelfieSelected(e.target.files?.[0])}
                      />
                      <button
                        type="button"
                        disabled={!agreed}
                        onClick={() => inputRef.current?.click()}
                        className="py-3 px-8 rounded-[var(--radius-pill)] font-medium disabled:opacity-40"
                        style={{ background: "var(--accent)", color: "var(--bg-0)" }}
                      >
                        Take a selfie
                      </button>
                    </>
                  )}
    </div>
  );
}
