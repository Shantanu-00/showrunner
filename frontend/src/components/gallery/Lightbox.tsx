"use client";

import type { ReactNode } from "react";
import type { MediaDoc } from "@/lib/types";

/** Full-screen `display_1600` viewer (spec 04 §3) shared by the public gallery and the private
 * album — the two surfaces attach different action rows (Why this photo? vs share/save/veto). */
export function Lightbox({
  media,
  onClose,
  actions,
}: {
  media: MediaDoc;
  onClose: () => void;
  actions?: ReactNode;
}) {
  const src = media.displayUri ?? media.thumbUri ?? "";
  return (
    <div className="fixed inset-0 z-50 flex flex-col" style={{ background: "rgba(0,0,0,0.92)" }}>
      <div className="flex justify-end p-4">
        <button
          type="button"
          onClick={onClose}
          className="text-3xl leading-none"
          style={{ color: "var(--ivory)" }}
          aria-label="Close"
        >
          ×
        </button>
      </div>

      <div className="flex-1 flex items-center justify-center px-2 overflow-hidden">
        {src ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={src}
            alt={media.curator?.caption ?? ""}
            className="max-h-full max-w-full object-contain rounded-[var(--radius-card)]"
          />
        ) : (
          <div className="w-full h-64 skeleton-shimmer rounded-[var(--radius-card)]" />
        )}
      </div>

      {media.curator?.caption && (
        <p
          className="text-center px-6 pb-2 font-[var(--font-display)] italic text-lg"
          style={{ color: "var(--ivory)" }}
        >
          {media.curator.caption}
        </p>
      )}

      {actions && <div className="px-6 pb-8 pt-2 flex flex-wrap gap-3 justify-center">{actions}</div>}
    </div>
  );
}
