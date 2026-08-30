"use client";

import { useEffect, useRef, useState, type ElementType } from "react";
import { Camera, Sparkles, Clock, AlertTriangle, CheckCircle2, ShieldCheck, X, Scan } from "lucide-react";
import { ApiError, enrollPerson } from "@/lib/api";
import { refreshClaims } from "@/lib/firebase";
import { useHaptics } from "@/lib/useHaptics";
import { GlowButton } from "@/components/atoms/GlowButton";
import type { EnrollOutcome } from "@/lib/types";

type Phase = "consent" | "enrolling" | "linked" | "held_for_review" | "pending_host_approval" | "error";

const SCAN_STEPS = [
  "Analyzing event moments...",
  "Extracting biometric embeddings...",
  "Cross-referencing face clusters...",
  "Curating your private album...",
];

export function EnrollRitual({
  eventId,
  onEnrolled,
  onPending,
  onCancel,
}: {
  eventId: string;
  onEnrolled: (personId: string, outcome: EnrollOutcome) => void;
  /** Called for `pending_host_approval` — the selfie matched somebody already enrolled */
  onPending: () => void;
  onCancel: () => void;
}) {
  const [agreed, setAgreed] = useState(false);
  const [phase, setPhase] = useState<Phase>("consent");
  const [message, setMessage] = useState<string | null>(null);
  const [scanStepIndex, setScanStepIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const { tapHaptic, successHaptic, alertHaptic } = useHaptics();

  // Dynamic status text ticker during scanning
  useEffect(() => {
    if (phase !== "enrolling") return;
    const interval = setInterval(() => {
      setScanStepIndex((prev) => (prev + 1) % SCAN_STEPS.length);
    }, 1400);
    return () => clearInterval(interval);
  }, [phase]);

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
    tapHaptic();
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
        successHaptic();
        onPending();
        setPhase("pending_host_approval");
        return;
      }
      await refreshClaims();
      successHaptic();
      onEnrolled(res.personId!, res.outcome);
      setPhase(res.outcome);
    } catch (err) {
      alertHaptic();
      setMessage(
        err instanceof ApiError
          ? "Couldn't reach the director yet — try again in a moment."
          : "Something went wrong — try again."
      );
      setPhase("error");
    }
  }

  const doneScreen = (Icon: ElementType, title: string, isError = false) => (
    <div className="max-w-sm mx-auto flex flex-col items-center animate-spring-in">
      <div
        className={`w-20 h-20 rounded-full flex items-center justify-center mb-5 shadow-2xl ${
          isError
            ? "bg-red-500/20 text-red-400 border border-red-500/30"
            : "bg-[var(--emerald-live)]/20 text-[var(--emerald-live)] border border-[var(--emerald-live)]/30"
        }`}
      >
        <Icon className="w-10 h-10 stroke-[2]" />
      </div>
      <h2 className="font-[family-name:var(--font-display)] text-2xl sm:text-3xl font-semibold text-[var(--text-primary)] mb-2">
        {title}
      </h2>
      {message && (
        <p className="text-xs text-[var(--text-secondary)] leading-relaxed max-w-xs mb-6">
          {message}
        </p>
      )}
      <GlowButton
        variant="primary"
        size="md"
        onClick={onCancel}
        className="w-full mt-2"
      >
        Done
      </GlowButton>
    </div>
  );

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Find My Photos Biometric Matcher"
      className="fixed inset-0 z-50 flex flex-col items-center justify-center px-6 text-center bg-slate-950/90 backdrop-blur-2xl animate-fadeIn"
    >
      <button
        type="button"
        onClick={onCancel}
        className="absolute top-6 right-6 p-2.5 rounded-full glass-card hover:bg-white/15 text-[var(--text-secondary)] hover:text-white transition-all cursor-pointer"
        aria-label="Close modal"
      >
        <X className="w-5 h-5 stroke-[2]" />
      </button>

      {phase === "linked" && doneScreen(CheckCircle2, "Your Album is Ready")}
      {phase === "held_for_review" && doneScreen(Clock, "Confirmation in Progress")}
      {phase === "pending_host_approval" && doneScreen(Clock, "Waiting for Host Approval")}
      {phase === "error" && doneScreen(AlertTriangle, "Enrollment Failed", true)}

      {/* High-Tech Radar Scanning State */}
      {phase === "enrolling" && (
        <div className="flex flex-col items-center max-w-sm animate-spring-in">
          <div className="relative w-48 h-48 rounded-full mb-6 flex items-center justify-center overflow-hidden border border-[var(--accent)]/40 shadow-[0_0_36px_rgba(99,102,241,0.35)]">
            {/* Viewfinder corner brackets */}
            <div className="absolute inset-2 rounded-full border border-dashed border-white/20 animate-spin" style={{ animationDuration: "16s" }} />
            
            {/* Radar Scan Line */}
            <div className="radar-scan-line" />

            {/* Central glowing orb */}
            <div className="w-36 h-36 rounded-full bg-slate-900/80 flex flex-col items-center justify-center border border-white/10 shadow-inner">
              <Scan className="w-12 h-12 text-[var(--accent)] animate-pulse stroke-[1.8]" />
            </div>
          </div>

          <h3 className="font-[family-name:var(--font-display)] text-2xl font-semibold text-[var(--text-primary)] mb-2">
            Face Match Engine
          </h3>
          <p className="text-xs font-mono text-[var(--accent)] tracking-wider mb-1 tabular-nums animate-pulse">
            {SCAN_STEPS[scanStepIndex]}
          </p>
          <p className="text-[11px] text-[var(--text-tertiary)] max-w-xs">
            InsightFace 512-D vector matching in progress
          </p>
        </div>
      )}

      {/* Consent & Viewfinder Screen */}
      {phase === "consent" && (
        <div className="max-w-sm mx-auto flex flex-col items-center animate-spring-in">
          {/* Circular Camera Viewfinder Frame with Soft Glowing Ring & Ambient Pulse */}
          <div className="relative w-40 h-40 mb-6 flex items-center justify-center">
            {/* Ambient Pulse Ring */}
            <div className="absolute inset-0 rounded-full border-2 border-[var(--accent)]/40 viewfinder-pulse" />
            
            {/* Rotating Conic Gradient Edge */}
            <div
              className="absolute -inset-1 rounded-full opacity-70 blur-[2px] animate-spin"
              style={{
                background: `conic-gradient(from 0deg, var(--accent), transparent 60%, var(--accent-soft))`,
                animationDuration: "6s",
              }}
              aria-hidden
            />

            {/* Inner Viewfinder Center */}
            <div className="relative z-10 w-36 h-36 rounded-full flex flex-col items-center justify-center bg-slate-950/90 border border-white/15 shadow-2xl">
              <Camera className="w-12 h-12 text-[var(--accent)] stroke-[1.8] mb-1" />
              <span className="text-[10px] font-mono uppercase tracking-[0.18em] text-[var(--text-secondary)]">
                VIEWFINDER
              </span>
            </div>
          </div>

          <h2 className="font-[family-name:var(--font-display)] text-2xl sm:text-3xl font-semibold text-gold-gradient mb-2">
            Find My Photos
          </h2>
          <p className="text-xs text-[var(--text-secondary)] mb-5 leading-relaxed max-w-xs">
            Take a quick selfie. Our autonomous director securely indexes your face embeddings to match and assemble every moment you appear in across the event.
          </p>

          <label className="flex items-start gap-3 text-left p-3.5 rounded-2xl glass-card mb-6 text-xs text-[var(--text-primary)] cursor-pointer hover:border-[var(--accent)]/40 transition-colors">
            <input
              type="checkbox"
              checked={agreed}
              onChange={(e) => {
                tapHaptic();
                setAgreed(e.target.checked);
              }}
              className="mt-0.5 w-4 h-4 rounded accent-[var(--accent)] cursor-pointer"
            />
            <span className="leading-relaxed text-[11px] text-[var(--text-secondary)]">
              I consent to biometric face matching for this event. Data is sandboxed, encrypted, and automatically expunged after event wrapping.
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

          <GlowButton
            variant="primary"
            size="lg"
            disabled={!agreed}
            onClick={() => inputRef.current?.click()}
            icon={Camera}
            fullWidth
          >
            Open Camera & Match Selfie
          </GlowButton>
        </div>
      )}
    </div>
  );
}
