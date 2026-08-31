"use client";

import { useEffect, useState } from "react";
import { Camera, User, Upload, Clock, Sparkles } from "lucide-react";
import { refreshClaims } from "@/lib/firebase";
import { useHaptics } from "@/lib/useHaptics";
import { GlowButton } from "@/components/atoms/GlowButton";
import {
  clearPendingEnrollment,
  markPendingEnrollment,
  pendingEnrollment,
} from "./pendingEnrollment";
import { EnrollRitual } from "./EnrollRitual";
import { AlbumGrid } from "./AlbumGrid";
import { MyUploads } from "./MyUploads";
import { SettingsRows } from "./SettingsRows";

type Segment = "album" | "uploads";

export function MeTab({ eventId, uid }: { eventId: string; uid: string }) {
  const [personId, setPersonId] = useState<string | null>(null);
  const [claimHeld, setClaimHeld] = useState(false);
  const [segment, setSegment] = useState<Segment>("album");
  const [showRitual, setShowRitual] = useState(false);
  const [deleted, setDeleted] = useState(false);
  const { tapHaptic, successHaptic } = useHaptics();

  useEffect(() => {
    void refreshClaims().then((claims) => {
      const claimed = claims.personId ?? null;
      setPersonId(claimed);
      if (claimed) {
        clearPendingEnrollment(eventId);
        setClaimHeld(false);
        return;
      }
      const pending = pendingEnrollment(eventId);
      if (pending) {
        setPersonId(pending.personId);
        setClaimHeld(true);
      }
    });
  }, [uid, eventId]);

  // Approval arrives as a **custom claim**, and a custom claim is only visible to this device after
  // its ID token is refreshed — there is no document the guest is allowed to watch that says "you're
  // approved". So while a claim is held, and only then, re-read the token on a slow cadence. Without
  // this the host approving somebody changed nothing on their phone until they thought to reload the
  // page, which is precisely what they were told they would not have to do ("this album fills in the
  // moment they approve"). Stops the instant it succeeds, and pauses with the tab.
  useEffect(() => {
    if (!claimHeld) return;
    let cancelled = false;

    const check = async () => {
      if (cancelled || document.visibilityState !== "visible") return;
      const claims = await refreshClaims();
      if (cancelled || !claims.personId) return;
      setPersonId(claims.personId);
      setClaimHeld(false);
      clearPendingEnrollment(eventId);
      successHaptic();
    };

    const interval = setInterval(() => void check(), 10_000);
    // ...and once on the way back from wherever the guest went while waiting.
    const onVisible = () => void check();
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      cancelled = true;
      clearInterval(interval);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [claimHeld, eventId, successHaptic]);

  if (deleted) {
    return (
      <div className="text-center mt-16 px-6 py-12 rounded-3xl glass-card mx-4 border border-white/10 shadow-2xl animate-spring-in">
        <p className="text-sm text-[var(--text-secondary)]">
          Your personal data has been erased. You can continue browsing or uploading anonymously.
        </p>
      </div>
    );
  }

  return (
    <div className="max-w-2xl mx-auto">
      {/* Floating Segment Control */}
      <div className="flex justify-center px-4 pt-2 pb-4">
        <div className="flex items-center p-1 rounded-full glass-pill bg-slate-950/80 border border-white/10 shadow-lg">
          <button
            type="button"
            onClick={() => {
              tapHaptic();
              setSegment("album");
            }}
            className={`flex items-center gap-1.5 text-xs px-4 py-2 rounded-full transition-all duration-200 cursor-pointer min-h-[38px] active:scale-95 ${
              segment === "album"
                ? "bg-[var(--accent)] text-slate-950 font-bold shadow-md"
                : "text-[var(--text-secondary)] hover:text-white"
            }`}
          >
            <User className="w-3.5 h-3.5" />
            <span>My Album</span>
          </button>
          <button
            type="button"
            onClick={() => {
              tapHaptic();
              setSegment("uploads");
            }}
            className={`flex items-center gap-1.5 text-xs px-4 py-2 rounded-full transition-all duration-200 cursor-pointer min-h-[38px] active:scale-95 ${
              segment === "uploads"
                ? "bg-[var(--accent)] text-slate-950 font-bold shadow-md"
                : "text-[var(--text-secondary)] hover:text-white"
            }`}
          >
            <Upload className="w-3.5 h-3.5" />
            <span>My Uploads</span>
          </button>
        </div>
      </div>

      {segment === "album" &&
        (claimHeld ? (
          <div className="text-center mt-12 px-6 py-12 rounded-3xl glass-card mx-4 border border-white/10 shadow-2xl animate-spring-in">
            <div className="p-3.5 rounded-full bg-[var(--accent)]/15 text-[var(--accent)] w-14 h-14 flex items-center justify-center mx-auto mb-4 border border-[var(--accent)]/30 shadow-lg">
              <Clock className="w-7 h-7 animate-pulse" />
            </div>
            <h3 className="font-[family-name:var(--font-display)] text-xl font-semibold text-[var(--text-primary)] mb-1.5">
              The host is confirming it&apos;s you
            </h3>
            <p className="text-xs text-[var(--text-secondary)] max-w-sm mx-auto leading-relaxed">
              To ensure total privacy, an event host confirms your face match. Your own photos are already visible in{" "}
              <span className="text-white font-medium">My Uploads</span> — this matched album fills in the moment they approve.
            </p>
          </div>
        ) : personId ? (
          <AlbumGrid eventId={eventId} personId={personId} />
        ) : (
          <div className="px-5 mt-6 text-center animate-spring-in">
            <div className="p-8 sm:p-10 rounded-3xl glass-card border border-white/10 max-w-md mx-auto shadow-2xl flex flex-col items-center">
              <div className="relative w-20 h-20 rounded-full bg-[var(--accent)]/15 flex items-center justify-center text-[var(--accent)] mb-5 border border-[var(--accent)]/30 shadow-[0_0_24px_rgba(99,102,241,0.25)]">
                <Camera className="w-9 h-9 stroke-[2]" />
                <div className="absolute inset-0 rounded-full border border-[var(--accent)]/40 animate-ping opacity-30" />
              </div>
              <h3 className="font-[family-name:var(--font-display)] text-2xl sm:text-3xl font-semibold text-gold-gradient mb-2">
                Unlock Your Face Album
              </h3>
              <p className="text-xs text-[var(--text-secondary)] mb-7 max-w-xs leading-relaxed">
                Take a 2-second selfie. Our AI face indexer will instantly search and gather every photo you appear in across the entire event.
              </p>
              <GlowButton
                variant="primary"
                size="lg"
                onClick={() => setShowRitual(true)}
                icon={Sparkles}
                fullWidth
              >
                Take a Selfie to Match
              </GlowButton>
            </div>
          </div>
        ))}

      {segment === "uploads" && <MyUploads eventId={eventId} uid={uid} />}

      <SettingsRows
        eventId={eventId}
        onDeleted={() => {
          clearPendingEnrollment(eventId);
          setClaimHeld(false);
          setPersonId(null);
          setDeleted(true);
        }}
      />

      {showRitual && (
        <EnrollRitual
          eventId={eventId}
          onCancel={() => setShowRitual(false)}
          onEnrolled={(id, outcome) => {
            setPersonId(id);
            const held = outcome === "held_for_review";
            setClaimHeld(held);
            if (held) markPendingEnrollment(eventId, id);
            else clearPendingEnrollment(eventId);
          }}
          onPending={() => {
            setClaimHeld(true);
            markPendingEnrollment(eventId, null);
          }}
        />
      )}
    </div>
  );
}
