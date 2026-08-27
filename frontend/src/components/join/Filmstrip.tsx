"use client";

import type { OutboxItem } from "@/lib/types";
import { retryItem } from "@/lib/uploadManager";

const CHIP_LABEL: Record<string, string> = {
  queued: "Sending to the director…",
  url_issued: "Sending to the director…",
  uploading: "Sending to the director…",
  done: "live 🎉",
  failed: "Failed",
};

function Chip({ item }: { item: OutboxItem }) {
  const label = CHIP_LABEL[item.state] ?? item.state;
  const isPending = item.state === "queued" || item.state === "url_issued" || item.state === "uploading";
  return (
    <div className="flex flex-col items-center gap-1 shrink-0">
      <div
        className="relative w-16 h-16 rounded-[var(--radius-card)] overflow-hidden flex items-center justify-center"
        style={{ border: "var(--hairline)", background: "var(--bg-1)" }}
      >
        {isPending && (
          <div
            className="absolute inset-0 animate-pulse"
            style={{ background: "linear-gradient(120deg, transparent, rgb(212 175 106 / 0.25), transparent)" }}
          />
        )}
        <span className="text-xs" aria-hidden>
          {item.kind === "video" ? "🎬" : "📷"}
        </span>
      </div>
      <span
        className="text-[10px] text-center max-w-16"
        style={{ color: item.state === "failed" ? "var(--danger)" : "var(--ink-muted)" }}
      >
        {label}
      </span>
      {item.state === "failed" && (
        <button
          type="button"
          onClick={() => void retryItem(item.clientMediaId)}
          className="text-[10px] underline"
          style={{ color: "var(--accent)" }}
        >
          Retry
        </button>
      )}
    </div>
  );
}

export function Filmstrip({ items }: { items: OutboxItem[] }) {
  if (items.length === 0) return null;
  return (
    <div className="flex gap-3 overflow-x-auto px-4 py-3">
      {items.map((item) => (
        <Chip key={item.clientMediaId} item={item} />
      ))}
    </div>
  );
}
