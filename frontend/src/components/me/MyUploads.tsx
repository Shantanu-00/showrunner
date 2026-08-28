"use client";

import { useEffect, useState } from "react";
import type { ConsentRing, MediaDoc } from "@/lib/types";
import { listenMyUploads } from "@/lib/firestore";
import { PadlockSheet } from "@/components/consent/PadlockSheet";
import { useAuthedImage } from "@/lib/useAuthedImage";

const PENDING_LABEL: Record<string, string> = {
  awaiting_upload: "Sending to the director…",
  uploaded: "Sending to the director…",
  processing: "The Curator is judging your shot…",
  rejected: "Couldn't process this one",
  quarantined: "Needs another look",
  abandoned: "Didn't make it",
};

function ringOf(media: MediaDoc): ConsentRing {
  return media.visibility ?? (media.consent.ring as ConsentRing) ?? "pool";
}

/** Spec 12 §7's filmstrip states, extended to the settled ones this list also shows —
 * `indexed` is a processing state, not a ring, so it must never stand in for the ring label. */
function stateLabel(media: MediaDoc, ring: ConsentRing): string {
  if (media.status !== "indexed") return PENDING_LABEL[media.status] ?? media.status;
  if (ring === "public") return "live 🎉";
  if (ring === "self") return "just for me";
  return "in the pool";
}

function padlockGlyph(ring: ConsentRing): string {
  if (ring === "public") return "📺";
  if (ring === "self") return "🙈";
  return "🔒";
}

/** My uploads (spec 04 §3, spec 12 §5.2): every upload with its padlock chip = consent moment
 * C3 — tap to flip the ring anytime, retroactive within seconds via `recompute_visibility`. */
export function MyUploads({ eventId, uid }: { eventId: string; uid: string }) {
  const [items, setItems] = useState<MediaDoc[]>([]);
  const [editing, setEditing] = useState<MediaDoc | null>(null);

  useEffect(() => {
    return listenMyUploads(eventId, uid, setItems, () => setItems([]));
  }, [eventId, uid]);

  if (items.length === 0) {
    return (
      <p className="text-center mt-16 px-5" style={{ color: "var(--ink-muted)" }}>
        Nothing uploaded yet this session — the camera tab is always one tap away.
      </p>
    );
  }

  return (
    <>
      <ul className="px-4 mt-2 space-y-2">
        {items.map((media) => {
          const ring = ringOf(media);
          const label = stateLabel(media, ring);
          return (
            <li
              key={media.mediaId}
              className="flex items-center gap-3 p-2 rounded-[var(--radius-card)]"
              style={{ border: "var(--hairline)", background: "var(--bg-1)" }}
            >
              <UploadThumb eventId={eventId} media={media} />
              <span className="flex-1 text-sm" style={{ color: "var(--ink-muted)" }}>
                {label}
              </span>
              <button
                type="button"
                onClick={() => setEditing(media)}
                className="text-xl w-11 h-11 flex items-center justify-center"
                aria-label="Change who can see this photo"
              >
                {padlockGlyph(ring)}
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
    <div className="w-12 h-12 rounded-[var(--radius-card)] overflow-hidden shrink-0" style={{ background: "var(--bg-0)" }}>
      {src ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={src} alt="" className="w-full h-full object-cover" />
      ) : (
        <div className="w-full h-full skeleton-shimmer" />
      )}
    </div>
  );
}
