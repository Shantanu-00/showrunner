"use client";

import { useEffect, useState } from "react";
import { listenMedia } from "@/lib/firestore";
import type { DoneLedgerEntry, MediaDoc, OutboxItem } from "@/lib/types";
import { retryItem } from "@/lib/uploadManager";

/** Most recent done-ledger entries to keep tailing — an hours-long event shouldn't grow the
 * filmstrip without bound; the ledger's own key order (ULID) is already chronological. */
const MAX_DONE_CHIPS = 6;

const PENDING_LABEL: Record<string, string> = {
  queued: "Sending to the director…",
  url_issued: "Sending to the director…",
  uploading: "Sending to the director…",
};

/** Spec 12 §4's wait-state copy table — the same agent-verb micro-copy, driven by the media
 * doc's real `stages` field instead of a generic spinner (the no-spinner rule's whole point).
 * `pending` decides the shimmer, so it has to agree with the label rather than assume "not
 * indexed yet" is the only terminal state — a permanent failure or a review hold is also done
 * shimmering even though it never reaches `indexed`. */
function describeMedia(media: MediaDoc | null): { label: string; pending: boolean } {
  if (!media) return { label: "Sending to the director…", pending: true };
  const stages = media.stages ?? {};
  if (media.status === "indexed") {
    if (media.visibility === "public") return { label: "live 🎉", pending: false };
    if (media.visibility === "self") return { label: "🔒 just for you", pending: false };
    return { label: "🔒 in the pool", pending: false };
  }
  if (media.status === "quarantined" || media.status === "rejected" || media.status === "abandoned") {
    return { label: "Held for review", pending: false };
  }
  // Spec 03 §6's asymmetric failure design: a *permanent* stage failure (model refusal, twice
  // schema-invalid) never quarantines — the photo just never reaches `indexed`, so it would
  // otherwise sit here forever reading "still processing" for a mishap that already happened.
  if (Object.values(stages).some((s) => s === "failed_permanent")) {
    return { label: "Kept in your album", pending: false };
  }
  if (stages.curate === "pending" || stages.curate == null) {
    return { label: "The Curator is judging your shot…", pending: true };
  }
  if (stages.faces === "pending" || stages.faces == null) {
    return { label: "Looking for you in the archives…", pending: true };
  }
  if (stages.safety === "pending" || stages.safety == null) {
    return { label: "The Guardian is giving it one last look…", pending: true };
  }
  return { label: "Sending to the director…", pending: true };
}

function PendingChip({ item }: { item: OutboxItem }) {
  const label = PENDING_LABEL[item.state] ?? item.state;
  return (
    <ChipShell isPending kind={item.kind} label={label} />
  );
}

function DoneChip({ eventId, entry }: { eventId: string; entry: DoneLedgerEntry }) {
  const [media, setMedia] = useState<MediaDoc | null>(null);

  useEffect(() => {
    return listenMedia(eventId, entry.clientMediaId, setMedia, () => {});
  }, [eventId, entry.clientMediaId]);

  const { label, pending } = describeMedia(media);
  return <ChipShell isPending={pending} label={label} thumbDataUrl={entry.thumbDataUrl} />;
}

function ChipShell({
  isPending,
  label,
  kind,
  thumbDataUrl,
  failed,
  onRetry,
}: {
  isPending: boolean;
  label: string;
  kind?: OutboxItem["kind"];
  thumbDataUrl?: string;
  failed?: boolean;
  onRetry?: () => void;
}) {
  return (
    <div className="flex flex-col items-center gap-1 shrink-0">
      <div
        className="relative w-16 h-16 rounded-[var(--radius-card)] overflow-hidden flex items-center justify-center"
        style={{ border: "var(--hairline)", background: "var(--bg-1)" }}
      >
        {thumbDataUrl ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={thumbDataUrl} alt="" className="absolute inset-0 w-full h-full object-cover" />
        ) : (
          <span className="text-xs" aria-hidden>
            {kind === "video" ? "🎬" : "📷"}
          </span>
        )}
        {isPending && <div className="absolute inset-0 skeleton-shimmer" />}
      </div>
      <span
        className="text-[10px] text-center max-w-16"
        style={{ color: failed ? "var(--danger)" : "var(--ink-muted)" }}
      >
        {label}
      </span>
      {failed && onRetry && (
        <button type="button" onClick={onRetry} className="text-[10px] underline" style={{ color: "var(--accent)" }}>
          Retry
        </button>
      )}
    </div>
  );
}

export function Filmstrip({
  items,
  doneItems,
  eventId,
}: {
  items: OutboxItem[];
  doneItems: DoneLedgerEntry[];
  eventId: string;
}) {
  const failed = items.filter((i) => i.state === "failed");
  const pending = items.filter((i) => i.state !== "failed");
  const recentDone = doneItems.slice(-MAX_DONE_CHIPS);

  if (failed.length === 0 && pending.length === 0 && recentDone.length === 0) return null;

  return (
    <div className="flex gap-3 overflow-x-auto px-4 py-3">
      {failed.map((item) => (
        <ChipShell
          key={item.clientMediaId}
          isPending={false}
          failed
          label="Failed"
          kind={item.kind}
          onRetry={() => void retryItem(item.clientMediaId)}
        />
      ))}
      {pending.map((item) => (
        <PendingChip key={item.clientMediaId} item={item} />
      ))}
      {recentDone.map((entry) => (
        <DoneChip key={entry.clientMediaId} eventId={eventId} entry={entry} />
      ))}
    </div>
  );
}
