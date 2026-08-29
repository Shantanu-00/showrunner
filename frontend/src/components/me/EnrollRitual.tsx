"use client";

import { useRef, useState, type ElementType } from "react";
import { Camera, Sparkles, Clock, AlertTriangle, CheckCircle2, ShieldCheck, X } from "lucide-react";
import { ApiError, enrollPerson } from "@/lib/api";
import { refreshClaims } from "@/lib/firebase";
import type { EnrollOutcome } from "@/lib/types";

type Phase = "consent" | "enrolling" | "linked" | "held_for_review" | "pending_host_approval" | "error";

export function EnrollRitual({
  eventId,
  onEnrolled,
  onPending,
  onCancel,
}: {
  eventId: string;
  onEnrolled: (personId: string, outcome: EnrollOutcome) => void;
  /** Called for `pending_host_approval` — the selfie matched somebody already enrolled, so no person
   * was created and there is no `personId` to hand back. `/me` still has to remember that the ask
   * happened, or the next page load invites the same guest to enrol all over again. */
  onPending: () => void;
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
        onPending();
        setPhase("pending_host_approval");
        return;
      }
      // `refreshClaims()` is still worth the round trip even though a held enrollment grants no
      // `personId`: it is what picks the claim up on the one path that *does* grant immediately (a
      // magic-link redemption racing this screen), and a stale token is the more expensive mistake.
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

  const doneScreen = (Icon: ElementType, title: string, isError = false) => (
    <div className="max-w-sm mx-auto flex flex-col items-center">
      <div
        className={`w-16 h-16 rounded-full flex items-center justify-center mb-4 ${
          isError
            ? "bg-[var(--danger)]/20 text-[var(--danger)]"
            : "bg-[var(--gold-500)]/20 text-[var(--accent)]"
        }`}
      >
        <Icon className="w-8 h-8 stroke-[2]" />
      </div>
      <h2 className="font-[family-name:var(--font-display)] text-2xl font-medium text-[var(--ivory)] mb-2">
        {title}
      </h2>
      {message && (
        <p className="text-xs text-[var(--ink-muted)] leading-relaxed max-w-xs mb-6">
          {message}
        </p>
      )}
      <button
        type="button"
        onClick={onCancel}
        className="btn-primary py-3 px-8 text-sm font-semibold mt-4"
      >
        Done
      </button>
    </div>
  );

  return (
    <div
      className="fixed inset-0 z-50 flex flex-col items-center justify-center px-6 text-center bg-black/95 backdrop-blur-xl animate-fadeIn"
    >
      <button
        type="button"
        onClick={onCancel}
        className="absolute top-6 right-6 p-2 rounded-full hover:bg-white/10 text-[var(--ink-muted)] hover:text-white"
        aria-label="Close"
      >
        <X className="w-6 h-6 stroke-[2]" />
      </button>

      {phase === "linked" && doneScreen(CheckCircle2, "Your Album is Ready")}
      {phase === "held_for_review" && doneScreen(Clock, "Confirmation in Progress")}
      {phase === "pending_host_approval" && doneScreen(Clock, "Waiting for Host Approval")}
      {phase === "error" && doneScreen(AlertTriangle, "Enrollment Failed", true)}

      {phase === "enrolling" && (
        <div className="flex flex-col items-center max-w-sm">
          <div className="relative w-40 h-40 rounded-full mb-6 flex items-center justify-center">
            <div className="absolute inset-0 rounded-full border-2 border-[var(--gold-500)]/30 animate-ping" />
            <div className="w-36 h-36 rounded-full skeleton-shimmer flex items-center justify-center border border-[var(--hairline)]">
              <Sparkles className="w-10 h-10 text-[var(--accent)] animate-pulse" />
            </div>
          </div>
          <h3 className="font-[family-name:var(--font-display)] text-xl text-[var(--ivory)] mb-1">
            Analyzing Face Embeddings
          </h3>
          <p className="text-xs text-[var(--ink-muted)]">
            Searching event archives for matching moments…
          </p>
        </div>
      )}

      {phase === "consent" && (
        <div className="max-w-sm mx-auto flex flex-col items-center">
          <div className="relative w-36 h-36 mb-6">
            <div
              className="absolute inset-0 rounded-full consent-ring"
              style={{
                background: `conic-gradient(var(--accent) 0deg 90deg, transparent 90deg 360deg)`,
                mask: "radial-gradient(farthest-side, transparent calc(100% - 3px), #000 calc(100% - 3px))",
              }}
              aria-hidden
            />
            <div className="absolute inset-2 rounded-full flex items-center justify-center bg-[var(--bg-1)] border border-white/10 text-[var(--accent)] shadow-xl">
              <Camera className="w-12 h-12 stroke-[1.8]" />
            </div>
          </div>

          <h2 className="font-[family-name:var(--font-display)] text-2xl font-medium text-[var(--ivory)] mb-2">
            Face Match Enrollment
          </h2>
          <p className="text-xs text-[var(--ink-muted)] mb-5 leading-relaxed">
            Your selfie is securely indexed using InsightFace on Cloud Run to curate your personal album. Face embeddings remain sandboxed within this event and are automatically expunged.
          </p>

          <label className="flex items-start gap-3 text-left p-3.5 rounded-2xl glass-card mb-6 text-xs text-[var(--ivory)] cursor-pointer">
            <input
              type="checkbox"
              checked={agreed}
              onChange={(e) => setAgreed(e.target.checked)}
              className="mt-0.5 w-4 h-4 rounded accent-[var(--gold-500)]"
            />
            <span className="leading-relaxed">
              I consent to biometric matching for this event. Data is retained for the event duration + 30 days and can be deleted anytime in Settings.
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
            className="btn-primary w-full py-3.5 px-8 text-sm font-semibold flex items-center justify-center gap-2 disabled:opacity-40"
          >
            <Camera className="w-4 h-4 stroke-[2.2]" />
            <span>Open Camera & Take Selfie</span>
          </button>
        </div>
      )}
    </div>
  );
}
