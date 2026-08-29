"use client";

import { useEffect, useState } from "react";
import { Camera, User, Upload, Clock, Sparkles } from "lucide-react";
import { refreshClaims } from "@/lib/firebase";
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

  useEffect(() => {
    void refreshClaims().then((claims) => {
      const claimed = claims.personId ?? null;
      setPersonId(claimed);
      if (claimed) {
        // The claim exists, so the host approved: the local marker has done its job.
        clearPendingEnrollment(eventId);
        setClaimHeld(false);
        return;
      }
      // No claim. Either they never enrolled, or they enrolled and the host has not answered — and
      // only this browser knows which (spec 02 §3: a held claim deliberately grants nothing, so the
      // token cannot tell the two apart). Without this, a guest who just took a selfie is invited to
      // take another one, and the host collects duplicate review cards for one person.
      const pending = pendingEnrollment(eventId);
      if (pending) {
        setPersonId(pending.personId);
        setClaimHeld(true);
      }
    });
  }, [uid, eventId]);

  if (deleted) {
    return (
      <div className="text-center mt-16 px-6 py-12 rounded-2xl glass-card mx-4 border border-white/10">
        <p className="text-sm text-[var(--ink-muted)]">
          Your personal data has been erased. You can continue browsing or uploading anonymously.
        </p>
      </div>
    );
  }

  return (
    <div className="max-w-2xl mx-auto">
      <div className="flex gap-2 px-4 pt-2 pb-3">
        <div className="flex items-center p-1 rounded-full bg-white/5 border border-white/10">
          <button
            type="button"
            onClick={() => setSegment("album")}
            className={`flex items-center gap-1.5 text-xs px-4 py-1.5 rounded-full transition-all font-medium ${
              segment === "album"
                ? "bg-[var(--accent)] text-black font-semibold shadow-md"
                : "text-[var(--ink-muted)] hover:text-[var(--ivory)]"
            }`}
          >
            <User className="w-3.5 h-3.5" />
            <span>My Album</span>
          </button>
          <button
            type="button"
            onClick={() => setSegment("uploads")}
            className={`flex items-center gap-1.5 text-xs px-4 py-1.5 rounded-full transition-all font-medium ${
              segment === "uploads"
                ? "bg-[var(--accent)] text-black font-semibold shadow-md"
                : "text-[var(--ink-muted)] hover:text-[var(--ivory)]"
            }`}
          >
            <Upload className="w-3.5 h-3.5" />
            <span>My Uploads</span>
          </button>
        </div>
      </div>

      {/* `claimHeld` is checked before `personId` on purpose: a re-claim that matched an already-
          enrolled person creates no new person, so there is no id to hold — only a pending ask. */}
      {segment === "album" &&
        (claimHeld ? (
          <div className="text-center mt-12 px-6 py-10 rounded-2xl glass-card mx-4 border border-white/10">
            <div className="p-3 rounded-full bg-[var(--gold-500)]/15 text-[var(--accent)] w-12 h-12 flex items-center justify-center mx-auto mb-3">
              <Clock className="w-6 h-6 animate-pulse" />
            </div>
            <h3 className="font-[family-name:var(--font-display)] text-lg font-medium text-[var(--ivory)] mb-1">
              The host is confirming it&apos;s you
            </h3>
            <p className="text-xs text-[var(--ink-muted)] max-w-sm mx-auto leading-relaxed">
              Nobody gets someone else&apos;s album here, so a person who knows you checks your selfie
              first. Your own photos are already in{" "}
              <span className="text-[var(--ivory)]">My Uploads</span> — this album fills in the moment
              they say yes.
            </p>
          </div>
        ) : personId ? (
          <AlbumGrid eventId={eventId} personId={personId} />
        ) : (
          <div className="px-5 mt-6 text-center">
            <div className="p-8 rounded-3xl glass-card border border-[var(--hairline)] max-w-md mx-auto shadow-2xl flex flex-col items-center">
              <div className="w-16 h-16 rounded-full bg-[var(--gold-500)]/15 flex items-center justify-center text-[var(--accent)] mb-4 border border-[var(--gold-500)]/30">
                <Camera className="w-8 h-8 stroke-[2]" />
              </div>
              <h3 className="font-[family-name:var(--font-display)] text-2xl font-medium text-[var(--ivory)] mb-2">
                Unlock Your Face Album
              </h3>
              <p className="text-xs text-[var(--ink-muted)] mb-6 max-w-xs leading-relaxed">
                Take a quick selfie and our AI face indexer will automatically gather every photo you appear in across the entire event.
              </p>
              <button
                type="button"
                onClick={() => setShowRitual(true)}
                className="btn-primary w-full py-3 px-6 flex items-center justify-center gap-2 text-sm"
              >
                <Sparkles className="w-4 h-4 stroke-[2.2]" />
                <span>Take a Selfie to Match</span>
              </button>
            </div>
          </div>
        ))}

      {segment === "uploads" && <MyUploads eventId={eventId} uid={uid} />}

      <SettingsRows
        eventId={eventId}
        onDeleted={() => {
          // Deleting their data withdraws the pending ask too, so the marker must not outlive it and
          // leave them looking at "the host is confirming it's you" for a claim that no longer exists.
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
            // Matched somebody already enrolled, so no person was created and there is no id to
            // remember — only that the ask happened.
            setClaimHeld(true);
            markPendingEnrollment(eventId, null);
          }}
        />
      )}
    </div>
  );
}
