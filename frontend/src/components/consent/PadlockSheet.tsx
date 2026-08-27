"use client";

import { useState } from "react";
import type { ConsentRing } from "@/lib/types";
import { ApiError, setMediaConsent } from "@/lib/api";
import { RingChoiceSheet } from "./RingChoiceSheet";

/** Consent moment C3 (spec 02 §4) — per-photo, retroactive. Opened from the padlock chip on
 * any of the uploader's own photos in "My uploads". Effective within seconds via
 * `recompute_visibility`, run server-side by the same endpoint that also stores the flip. */
export function PadlockSheet({
  eventId,
  mediaId,
  currentRing,
  onDone,
  onCancel,
}: {
  eventId: string;
  mediaId: string;
  currentRing: ConsentRing;
  onDone: (ring: ConsentRing) => void;
  onCancel: () => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function handleConfirm(ring: ConsentRing) {
    setSaving(true);
    setError(null);
    try {
      await setMediaConsent(eventId, mediaId, ring);
      onDone(ring);
    } catch (err) {
      setError(
        err instanceof ApiError
          ? "Couldn't reach the director yet — try again in a moment."
          : "Something went wrong — try again."
      );
      setSaving(false);
    }
  }

  return (
    <div>
      <RingChoiceSheet
        title="Who can see this photo?"
        initialRing={currentRing}
        confirmLabel={saving ? "Saving…" : "Save"}
        onConfirm={(ring) => void handleConfirm(ring)}
        onCancel={onCancel}
      />
      {error && (
        <div
          className="fixed bottom-24 inset-x-4 z-[60] text-center text-sm py-2 rounded-[var(--radius-card)]"
          style={{ background: "var(--danger)", color: "var(--ivory)" }}
        >
          {error}
        </div>
      )}
    </div>
  );
}
