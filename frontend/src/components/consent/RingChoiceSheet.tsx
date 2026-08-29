"use client";

import { useState } from "react";
import { Users, Tv, Lock, Check } from "lucide-react";
import type { ConsentRing } from "@/lib/types";

/** The one 3-ring picker (spec 02 §4) shared by every consent moment that offers all three
 * choices in one tap: the send sheet (C1, per batch) and the padlock override (C3, per photo,
 * retroactive). */
export function RingChoiceSheet({
  title,
  initialRing = "pool",
  confirmLabel,
  onConfirm,
  onCancel,
}: {
  title: string;
  initialRing?: ConsentRing;
  confirmLabel: string;
  onConfirm: (ring: ConsentRing) => void;
  onCancel: () => void;
}) {
  const [selection, setSelection] = useState<ConsentRing>(initialRing);

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/75 backdrop-blur-sm animate-fadeIn">
      <div
        className="w-full max-w-md rounded-t-3xl p-6 pb-8 glass-card border-t border-[var(--hairline-accent)] shadow-2xl"
        style={{ background: "rgba(23, 16, 20, 0.96)" }}
      >
        <div className="w-12 h-1 rounded-full bg-white/20 mx-auto mb-5" />
        <h3 className="font-[family-name:var(--font-display)] text-xl font-medium text-[var(--ivory)] mb-1">
          {title}
        </h3>
        <p className="text-xs text-[var(--ink-muted)] mb-5">
          Select audience & visibility before uploading to the director.
        </p>

        <div className="space-y-3 mb-5">
          <button
            type="button"
            onClick={() => setSelection("pool")}
            className={`w-full text-left rounded-2xl p-4 transition-all duration-200 flex items-start gap-3.5 ${
              selection === "pool"
                ? "bg-[var(--bg-2)] border-2 border-[var(--accent)] shadow-md"
                : "bg-[var(--bg-1)]/60 border border-white/5 hover:border-white/20"
            }`}
          >
            <div
              className={`p-2.5 rounded-xl mt-0.5 ${
                selection === "pool"
                  ? "bg-[var(--accent)] text-black"
                  : "bg-white/5 text-[var(--ink-muted)]"
              }`}
            >
              <Users className="w-5 h-5 stroke-[2]" />
            </div>
            <div className="flex-1">
              <div className="flex items-center justify-between">
                <span className="font-semibold text-sm text-[var(--ivory)]">
                  Keep in the Pool
                </span>
                {selection === "pool" && (
                  <span className="p-0.5 rounded-full bg-[var(--accent)] text-black">
                    <Check className="w-3.5 h-3.5 stroke-[3]" />
                  </span>
                )}
              </div>
              <p className="text-xs text-[var(--ink-muted)] mt-0.5 leading-relaxed">
                Visible to you and people who appear in these photos.
              </p>
            </div>
          </button>

          <button
            type="button"
            onClick={() => setSelection("public")}
            className={`w-full text-left rounded-2xl p-4 transition-all duration-200 flex items-start gap-3.5 ${
              selection === "public"
                ? "bg-[var(--bg-2)] border-2 border-[var(--accent)] shadow-md"
                : "bg-[var(--bg-1)]/60 border border-white/5 hover:border-white/20"
            }`}
          >
            <div
              className={`p-2.5 rounded-xl mt-0.5 ${
                selection === "public"
                  ? "bg-[var(--accent)] text-black"
                  : "bg-white/5 text-[var(--ink-muted)]"
              }`}
            >
              <Tv className="w-5 h-5 stroke-[2]" />
            </div>
            <div className="flex-1">
              <div className="flex items-center justify-between">
                <span className="font-semibold text-sm text-[var(--ivory)]">
                  Share to the Big Screen
                </span>
                {selection === "public" && (
                  <span className="p-0.5 rounded-full bg-[var(--accent)] text-black">
                    <Check className="w-3.5 h-3.5 stroke-[3]" />
                  </span>
                )}
              </div>
              <p className="text-xs text-[var(--ink-muted)] mt-0.5 leading-relaxed">
                Eligible for the live kiosk wall and public gallery after dignity screening.
              </p>
            </div>
          </button>

          <button
            type="button"
            onClick={() => setSelection("self")}
            className={`w-full text-left rounded-2xl p-3.5 transition-all duration-200 flex items-center gap-3 ${
              selection === "self"
                ? "bg-[var(--bg-2)] border-2 border-[var(--accent)] text-[var(--ivory)]"
                : "bg-transparent border border-white/5 hover:border-white/15 text-[var(--ink-muted)]"
            }`}
          >
            <div className="p-1.5 rounded-lg bg-white/5">
              <Lock className="w-4 h-4" />
            </div>
            <span className="text-xs font-medium flex-1">
              Keep private (just for me)
            </span>
            {selection === "self" && (
              <span className="p-0.5 rounded-full bg-[var(--accent)] text-black">
                <Check className="w-3 h-3 stroke-[3]" />
              </span>
            )}
          </button>
        </div>

        <div className="flex gap-3">
          <button
            type="button"
            onClick={onCancel}
            className="flex-1 py-3 rounded-full btn-secondary text-sm font-medium"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={() => onConfirm(selection)}
            className="flex-1 py-3 rounded-full btn-primary text-sm font-semibold shadow-lg"
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
