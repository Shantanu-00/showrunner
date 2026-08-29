"use client";

import { useEffect, useState } from "react";
import { Camera, Video, Sparkles, Lock, ShieldAlert, CheckCircle2, RotateCw } from "lucide-react";
import { listenMedia } from "@/lib/firestore";
import type { DoneLedgerEntry, MediaDoc, OutboxItem } from "@/lib/types";
import { retryItem } from "@/lib/uploadManager";

const MAX_DONE_CHIPS = 6;

const PENDING_LABEL: Record<string, string> = {
  queued: "Sending…",
  url_issued: "Uploading…",
  uploading: "Uploading…",
};

function describeMedia(media: MediaDoc | null): { label: string; pending: boolean; iconType: "live" | "pool" | "self" | "hold" | "curating" } {
  if (!media) return { label: "Sending…", pending: true, iconType: "curating" };
  const stages = media.stages ?? {};
  if (media.status === "indexed") {
    if (media.visibility === "public") return { label: "Live on wall", pending: false, iconType: "live" };
    if (media.visibility === "self") return { label: "Just for you", pending: false, iconType: "self" };
    return { label: "In photo pool", pending: false, iconType: "pool" };
  }
  if (media.status === "quarantined" || media.status === "rejected" || media.status === "abandoned") {
    return { label: "Held for review", pending: false, iconType: "hold" };
  }
  if (Object.values(stages).some((s) => s === "failed_permanent")) {
    return { label: "Kept in album", pending: false, iconType: "self" };
  }
  if (stages.curate === "pending" || stages.curate == null) {
    return { label: "Curating…", pending: true, iconType: "curating" };
  }
  if (stages.faces === "pending" || stages.faces == null) {
    return { label: "Face matching…", pending: true, iconType: "curating" };
  }
  if (stages.safety === "pending" || stages.safety == null) {
    return { label: "Safety check…", pending: true, iconType: "curating" };
  }
  return { label: "Processing…", pending: true, iconType: "curating" };
}

function PendingChip({ item }: { item: OutboxItem }) {
  const label = PENDING_LABEL[item.state] ?? item.state;
  return <ChipShell isPending kind={item.kind} label={label} />;
}

function DoneChip({ eventId, entry }: { eventId: string; entry: DoneLedgerEntry }) {
  const [media, setMedia] = useState<MediaDoc | null>(null);

  useEffect(() => {
    return listenMedia(eventId, entry.clientMediaId, setMedia, () => {});
  }, [eventId, entry.clientMediaId]);

  const { label, pending, iconType } = describeMedia(media);
  return (
    <ChipShell
      isPending={pending}
      label={label}
      iconType={iconType}
      thumbDataUrl={entry.thumbDataUrl}
    />
  );
}

function ChipShell({
  isPending,
  label,
  kind,
  iconType,
  thumbDataUrl,
  failed,
  onRetry,
}: {
  isPending: boolean;
  label: string;
  kind?: OutboxItem["kind"];
  iconType?: "live" | "pool" | "self" | "hold" | "curating";
  thumbDataUrl?: string;
  failed?: boolean;
  onRetry?: () => void;
}) {
  return (
    <div className="flex flex-col items-center gap-1.5 shrink-0">
      <div className="relative w-16 h-16 rounded-2xl overflow-hidden glass-card border border-[var(--hairline)] flex items-center justify-center shadow-md">
        {thumbDataUrl ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={thumbDataUrl} alt="" className="absolute inset-0 w-full h-full object-cover" />
        ) : (
          <span className="text-[var(--ink-muted)]">
            {kind === "video" ? <Video className="w-6 h-6" /> : <Camera className="w-6 h-6" />}
          </span>
        )}

        {isPending && (
          <div className="absolute inset-0 skeleton-shimmer bg-black/40 flex items-center justify-center">
            <Sparkles className="w-5 h-5 text-[var(--accent)] animate-pulse" />
          </div>
        )}

        {!isPending && iconType === "live" && (
          <div className="absolute bottom-1 right-1 p-1 rounded-full bg-[var(--ok)] text-black shadow-md">
            <CheckCircle2 className="w-3 h-3 stroke-[3]" />
          </div>
        )}

        {!isPending && (iconType === "pool" || iconType === "self") && (
          <div className="absolute bottom-1 right-1 p-1 rounded-full bg-black/70 text-[var(--gold-300)] border border-white/10 shadow-md">
            <Lock className="w-3 h-3 stroke-[2.5]" />
          </div>
        )}

        {!isPending && iconType === "hold" && (
          <div className="absolute bottom-1 right-1 p-1 rounded-full bg-[var(--danger)] text-white shadow-md">
            <ShieldAlert className="w-3 h-3 stroke-[2.5]" />
          </div>
        )}
      </div>

      <span
        className="text-[10px] font-medium text-center max-w-16 truncate"
        style={{ color: failed ? "var(--danger)" : "var(--ink-muted)" }}
      >
        {label}
      </span>

      {failed && onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="flex items-center gap-1 text-[10px] font-medium text-[var(--accent)] hover:underline"
        >
          <RotateCw className="w-2.5 h-2.5" />
          <span>Retry</span>
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
    <div className="flex gap-3 overflow-x-auto px-4 py-3 bg-black/40 backdrop-blur-md border-t border-b border-white/5">
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
