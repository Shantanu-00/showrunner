"use client";

import { useEffect, useState } from "react";
import type { JustInSlot as JustInSlotType, MediaDoc } from "@/lib/types";
import { listenJustIn } from "@/lib/firestore";
import { mediaRenderUrl } from "@/lib/api";

/** `just_in` — the "your photo is on the wall" guarantee (spec 04 §4): recency-only, no score
 * term, no curation. A 96px filmstrip of whatever just went public. */
export function JustInSlot({ eventId, slot }: { eventId: string; slot: JustInSlotType }) {
  const [items, setItems] = useState<MediaDoc[]>([]);

  useEffect(() => {
    return listenJustIn(eventId, slot.liveWindowSec, setItems, () => setItems([]));
  }, [eventId, slot.liveWindowSec]);

  const hero = items[0];

  return (
    <div className="absolute inset-0" style={{ background: "var(--bg-0)" }}>
      {hero?.displayUri || hero?.thumbUri ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={mediaRenderUrl(eventId, hero.mediaId, hero.displayUri ? "display" : "thumb")}
          alt=""
          className="absolute inset-0 w-full h-full object-cover"
          style={{ filter: "blur(24px) brightness(0.5)" }}
        />
      ) : null}

      <div className="absolute inset-0 flex items-center justify-center px-[8%]">
        <p
          className="font-[family-name:var(--font-display)] text-5xl text-center"
          style={{ color: "var(--ivory)" }}
        >
          Just in
        </p>
      </div>

      <div
        className="absolute inset-x-0 bottom-[22%] h-24 flex items-center gap-2 px-[3%] overflow-hidden"
      >
        {items.length === 0 ? (
          <div className="w-24 h-24 rounded-[var(--radius-card)] skeleton-shimmer" />
        ) : (
          items.map((media) => (
            <div
              key={media.mediaId}
              className="relative w-24 h-24 shrink-0 rounded-[var(--radius-card)] overflow-hidden"
              style={{ border: "var(--hairline)" }}
            >
              {media.thumbUri && (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  src={mediaRenderUrl(eventId, media.mediaId, "thumb")}
                  alt=""
                  className="w-full h-full object-cover"
                />
              )}
              <span
                className="absolute bottom-1 left-1 text-[10px] px-1.5 py-0.5 rounded-[var(--radius-pill)]"
                style={{ background: "var(--accent)", color: "var(--bg-0)" }}
              >
                just now
              </span>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
