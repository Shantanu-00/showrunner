"use client";

import type { BatchConsent, ConsentRing } from "@/lib/types";
import { RingChoiceSheet } from "@/components/consent/RingChoiceSheet";

/** Consent moment C1 (spec 02 §4) — captured per-batch, on the send sheet itself. */
export function SendSheet({
  fileCount,
  onConfirm,
  onCancel,
}: {
  fileCount: number;
  onConfirm: (consent: BatchConsent) => void;
  onCancel: () => void;
}) {
  const consentFor = (ring: ConsentRing): BatchConsent => ({
    public: ring === "public",
    selfOnly: ring === "self",
  });

  return (
    <RingChoiceSheet
      title={`Sending ${fileCount} ${fileCount === 1 ? "photo" : "photos"}`}
      initialRing="pool"
      confirmLabel="Send"
      onConfirm={(ring) => onConfirm(consentFor(ring))}
      onCancel={onCancel}
    />
  );
}
