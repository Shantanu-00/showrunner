"use client";

import { useEffect, useState } from "react";
import { Tv, EyeOff, Lock, Sparkles, CheckCircle2, Sliders } from "lucide-react";
import type { ConsentRing, MediaDoc } from "@/lib/types";
import { listenMyUploads } from "@/lib/firestore";
import { PadlockSheet } from "@/components/consent/PadlockSheet";
import { useAuthedImage } from "@/lib/useAuthedImage";

const PENDING_LABEL: Record<string, string> = {
  awaiting_upload: "Sending to the director…",
  uploaded: "Uploaded, processing…",
  processing: "Curator evaluating shot…",
  rejected: "Quality threshold not met",
  quarantined: "Held for host review",
  abandoned: "Upload cancelled",
};

function ringOf(media: MediaDoc): ConsentRing {
  return media.visibility ?? (media.consent.ring as ConsentRing) ?? "pool";
}

function stateLabel(media: MediaDoc, ring: ConsentRing): string {
  if (media.status !== "indexed") return PENDING_LABEL[media.status] ?? media.status;
  if (ring === "public") return "Live on Kiosk & Gallery";
  if (ring === "self") return "Private (Just for you)";
  return "In Private Photo Pool";
}

export function MyUploads({ eventId, uid }: { eventId: string; uid: string }) {
  const [items, setItems] = useState<MediaDoc[]>([]);
  const [editing, setEditing] = useState<MediaDoc | null>(null);

  useEffect(() => {
    return listenMyUploads(eventId, uid, setItems, () => setItems([]));
  }, [eventId, uid]);

  if (items.length === 0) {
    return (
      <div className="text-center mt-12 px-6 py-12 rounded-2xl glass-card mx-4 border border-dashed border-white/10">
        <p className="text-xs text-[var(--ink-muted)]">
          No uploads yet from this device. Tap the center camera button to begin.
        </p>
      </div>
    );
  }

  return (
    <>
      <ul className="px-4 mt-2 space-y-2.5">
        {items.map((media) => {
          const ring = ringOf(media);
          const label = stateLabel(media, ring);
          return (
            <li
              key={media.mediaId}
              className="flex items-center gap-3 p-3 rounded-2xl glass-card shadow-sm hover:border-[var(--accent)] transition-all"
            >
              <UploadThumb eventId={eventId} media={media} />
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-1.5 mb-0.5">
                  {ring === "public" ? (
                    <span className="p-1 rounded-md bg-[var(--ok)]/15 text-[var(--ok)]">
                      <Tv className="w-3.5 h-3.5" />
                    </span>
                  ) : ring === "self" ? (
                    <span className="p-1 rounded-md bg-white/10 text-[var(--ink-muted)]">
                      <EyeOff className="w-3.5 h-3.5" />
                    </span>
                  ) : (
                    <span className="p-1 rounded-md bg-[var(--gold-500)]/15 text-[var(--accent)]">
                      <Lock className="w-3.5 h-3.5" />
                    </span>
                  )}
                  <span className="text-xs font-medium text-[var(--ivory)] truncate">
                    {label}
                  </span>
                </div>
                <p className="text-[11px] text-[var(--ink-muted)] truncate font-mono">
                  {media.mediaId.slice(0, 12)}…
                </p>
              </div>

              <button
                type="button"
                onClick={() => setEditing(media)}
                className="flex items-center gap-1 text-xs px-3 py-1.5 rounded-full bg-white/5 hover:bg-white/15 border border-white/10 text-[var(--gold-300)] transition-colors"
                aria-label="Change visibility"
                title="Change privacy ring"
              >
                <Sliders className="w-3.5 h-3.5" />
                <span className="hidden sm:inline">Visibility</span>
              </button>
            </li>
          );
        })}
      </ul>

      {editing && (
        <PadlockSheet
          eventId={eventId}
          mediaId={editing.mediaId}
          currentRing={ringOf(editing)}
          onDone={() => setEditing(null)}
          onCancel={() => setEditing(null)}
        />
      )}
    </>
  );
}

function UploadThumb({ eventId, media }: { eventId: string; media: MediaDoc }) {
  const src = useAuthedImage(eventId, media.thumbUri ? media.mediaId : null, "thumb");
  return (
    <div className="w-12 h-12 rounded-xl overflow-hidden shrink-0 glass-card bg-black/40 border border-white/10">
      {src ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={src} alt="" loading="lazy" decoding="async" className="w-full h-full object-cover" />
      ) : (
        <div className="w-full h-full skeleton-shimmer" />
      )}
    </div>
  );
}
