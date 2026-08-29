"use client";

import { useEffect, useState } from "react";
import { refreshClaims } from "@/lib/firebase";
import { EnrollRitual } from "./EnrollRitual";
import { AlbumGrid } from "./AlbumGrid";
import { MyUploads } from "./MyUploads";
import { SettingsRows } from "./SettingsRows";

type Segment = "album" | "uploads";

/** The Me tab (spec 12 §5.2 point 5): My Album | My uploads, the enroll ritual (C2) gating the
 * album until a selfie links this uid to a personId, and the settings danger zone at the foot. */
export function MeTab({ eventId, uid }: { eventId: string; uid: string }) {
  const [personId, setPersonId] = useState<string | null>(null);
  const [claimHeld, setClaimHeld] = useState(false);
  const [segment, setSegment] = useState<Segment>("album");
  const [showRitual, setShowRitual] = useState(false);
  const [deleted, setDeleted] = useState(false);

  useEffect(() => {
    void refreshClaims().then((claims) => setPersonId(claims.personId ?? null));
  }, [uid]);

  if (deleted) {
    return (
      <p className="text-center mt-16 px-5" style={{ color: "var(--ink-muted)" }}>
        Your data has been deleted. You can still upload as a new guest.
      </p>
    );
  }

  return (
    <div>
      <div className="flex gap-2 px-4 pt-2 pb-3">
        <SegmentButton label="My Album" active={segment === "album"} onClick={() => setSegment("album")} />
        <SegmentButton label="My uploads" active={segment === "uploads"} onClick={() => setSegment("uploads")} />
      </div>

      {segment === "album" &&
        (personId ? (
          claimHeld ? (
            <p className="text-center mt-16 px-5" style={{ color: "var(--ink-muted)" }}>
              The host is confirming it&rsquo;s you — your own shots are already here.
            </p>
          ) : (
            <AlbumGrid eventId={eventId} personId={personId} />
          )
        ) : (
          <div className="px-5 mt-8 text-center">
            <p className="text-5xl mb-4" aria-hidden>
              🤳
            </p>
            <h3 className="font-[family-name:var(--font-display)] text-xl mb-2" style={{ color: "var(--ivory)" }}>
              Unlock your album
            </h3>
            <p className="text-sm mb-6" style={{ color: "var(--ink-muted)" }}>
              Take a selfie and every photo of you finds its way here.
            </p>
            <button
              type="button"
              onClick={() => setShowRitual(true)}
              className="py-3 px-8 rounded-[var(--radius-pill)] font-medium"
              style={{ background: "var(--accent)", color: "var(--bg-0)" }}
            >
              Take a selfie
            </button>
          </div>
        ))}

      {segment === "uploads" && <MyUploads eventId={eventId} uid={uid} />}

      <SettingsRows eventId={eventId} onDeleted={() => setDeleted(true)} />

      {showRitual && (
        <EnrollRitual
          eventId={eventId}
          onCancel={() => setShowRitual(false)}
          onEnrolled={(id, outcome) => {
            setPersonId(id);
            setClaimHeld(outcome === "held_for_review");
          }}
        />
      )}
    </div>
  );
}

function SegmentButton({
  label,
  active,
  onClick,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="text-sm px-4 py-2 rounded-[var(--radius-pill)] min-h-11"
      style={{
        background: active ? "var(--accent)" : "transparent",
        color: active ? "var(--bg-0)" : "var(--ink-muted)",
        border: active ? "none" : "var(--hairline)",
      }}
    >
      {label}
    </button>
  );
}
