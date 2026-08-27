"use client";

import { useState } from "react";
import type { BatchConsent } from "@/lib/types";

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
  const [selection, setSelection] = useState<"pool" | "public" | "self">("pool");

  const consentFor = (choice: "pool" | "public" | "self"): BatchConsent => ({
    public: choice === "public",
    selfOnly: choice === "self",
  });

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/60">
      <div
        className="w-full max-w-md rounded-t-[var(--radius-banner)] p-5 pb-8"
        style={{ background: "var(--bg-1)", border: "var(--hairline)", borderBottom: "none" }}
      >
        <p className="text-sm mb-4" style={{ color: "var(--ink-muted)" }}>
          Sending {fileCount} {fileCount === 1 ? "photo" : "photos"}
        </p>

        <button
          type="button"
          onClick={() => setSelection("pool")}
          className="w-full text-left rounded-[var(--radius-card)] p-4 mb-3"
          style={{
            border: selection === "pool" ? "2px solid var(--accent)" : "var(--hairline)",
            background: "var(--bg-0)",
          }}
        >
          <div className="flex items-center gap-2 mb-1">
            <span aria-hidden>🔒</span>
            <span className="font-[var(--font-display)] text-lg">Keep in the pool</span>
          </div>
          <p className="text-sm" style={{ color: "var(--ink-muted)" }}>
            Visible to you and people in the photos.
          </p>
        </button>

        <button
          type="button"
          onClick={() => setSelection("public")}
          className="w-full text-left rounded-[var(--radius-card)] p-4 mb-3"
          style={{
            border: selection === "public" ? "2px solid var(--accent)" : "var(--hairline)",
            background: "var(--bg-0)",
          }}
        >
          <div className="flex items-center gap-2 mb-1">
            <span aria-hidden>📺</span>
            <span className="font-[var(--font-display)] text-lg">Share to the big screen</span>
          </div>
          <p className="text-sm" style={{ color: "var(--ink-muted)" }}>
            Eligible for the public gallery and kiosk after a dignity check.
          </p>
        </button>

        <button
          type="button"
          onClick={() => setSelection("self")}
          className="w-full text-left text-sm underline mb-6"
          style={{ color: "var(--ink-muted)" }}
        >
          {selection === "self" ? "✓ " : ""}Keep just for me
        </button>

        <div className="flex gap-3">
          <button
            type="button"
            onClick={onCancel}
            className="flex-1 py-3 rounded-[var(--radius-pill)]"
            style={{ color: "var(--ink-muted)" }}
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={() => onConfirm(consentFor(selection))}
            className="flex-1 py-3 rounded-[var(--radius-pill)] font-medium"
            style={{ background: "var(--accent)", color: "var(--bg-0)" }}
          >
            Send
          </button>
        </div>
      </div>
    </div>
  );
}
