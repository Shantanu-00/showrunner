"use client";

import type { ReactNode } from "react";
import { X } from "lucide-react";
import type { MediaDoc } from "@/lib/types";
import { MediaImg } from "@/lib/MediaImg";

export function Lightbox({
  eventId,
  media,
  onClose,
  actions,
}: {
  eventId: string;
  media: MediaDoc;
  onClose: () => void;
  actions?: ReactNode;
}) {
  const variant = media.displayUri ? "display" : media.thumbUri ? "thumb" : null;
  // A pool/self-tier photo always needs a token; a public one needs one too once the event is
  // invite-only, which is the choice `MediaImg` makes from the event's access mode.
  const isPublic = media.visibility === "public";

  return (
    <div className="fixed inset-0 z-50 flex flex-col justify-between bg-black/95 backdrop-blur-xl animate-fadeIn">
      <div className="flex justify-end p-4">
        <button
          type="button"
          onClick={onClose}
          className="p-2.5 rounded-full bg-white/10 hover:bg-white/20 text-white transition-colors"
          aria-label="Close photo viewer"
        >
          <X className="w-6 h-6 stroke-[2]" />
        </button>
      </div>

      <div className="flex-1 flex items-center justify-center px-4 overflow-hidden">
        {variant ? (
          <MediaImg
            eventId={eventId}
            mediaId={media.mediaId}
            variant={variant}
            forceAuthed={!isPublic}
            alt={media.curator?.caption ?? ""}
            className="max-h-[75vh] max-w-full object-contain rounded-2xl shadow-2xl border border-white/10"
            fallback={<div className="w-full max-w-md h-80 skeleton-shimmer rounded-2xl" />}
          />
        ) : (
          <div className="w-full max-w-md h-80 skeleton-shimmer rounded-2xl" />
        )}
      </div>

      <div className="p-6 max-w-2xl mx-auto w-full text-center">
        {media.curator?.caption && (
          <p className="font-[family-name:var(--font-display)] italic text-lg text-[var(--ivory)] mb-3 leading-relaxed">
            &ldquo;{media.curator.caption}&rdquo;
          </p>
        )}
        {actions && <div className="flex flex-wrap gap-3 justify-center items-center">{actions}</div>}
      </div>
    </div>
  );
}
