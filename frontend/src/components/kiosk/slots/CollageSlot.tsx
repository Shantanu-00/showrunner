"use client";

/** `collage` — displayed with a gold hairline frame + stage title (spec 12 §6). Collages are
 * built by a session that hasn't landed yet (P2 per EXECUTION-PLAN §0), so this renders the
 * honest cold-start shimmer rather than inventing a layout for a doc shape nothing writes yet. */
export function CollageSlot() {
  return (
    <div className="absolute inset-0 flex items-center justify-center p-[6%]" style={{ background: "var(--bg-0)" }}>
      <div
        className="w-full h-full rounded-[var(--radius-card)] skeleton-shimmer"
        style={{ border: "var(--hairline)" }}
      />
    </div>
  );
}
