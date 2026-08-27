"use client";

import { useState } from "react";
import { ApiError, createClaimLink, deleteMyData } from "@/lib/api";

/** Bottom-of-Me settings (spec 12 §5.2 point 5): the magic link (spec 02 §3.1) and the
 * danger-zone delete (spec 02 §5/§7) — both one tap, both honest about what they do. */
export function SettingsRows({
  eventId,
  onDeleted,
}: {
  eventId: string;
  onDeleted: () => void;
}) {
  const [linkState, setLinkState] = useState<"idle" | "loading" | "error">("idle");
  const [link, setLink] = useState<string | null>(null);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [deleteState, setDeleteState] = useState<"idle" | "loading" | "error">("idle");

  async function onSaveLink() {
    setLinkState("loading");
    try {
      const res = await createClaimLink(eventId);
      setLink(res.url);
      setLinkState("idle");
      if (navigator.share) {
        void navigator.share({ url: res.url, title: "My Showrunner album" }).catch(() => {});
      }
    } catch {
      setLinkState("error");
    }
  }

  async function onConfirmDelete() {
    setDeleteState("loading");
    try {
      await deleteMyData(eventId);
      onDeleted();
    } catch (err) {
      setDeleteState("error");
      if (!(err instanceof ApiError)) throw err;
    }
  }

  return (
    <div className="px-4 mt-8 space-y-2 pb-4">
      <button
        type="button"
        onClick={() => void onSaveLink()}
        className="w-full flex items-center gap-3 p-4 rounded-[var(--radius-card)] text-left"
        style={{ border: "var(--hairline)", background: "var(--bg-1)" }}
      >
        <span aria-hidden>🔗</span>
        <span className="flex-1 text-sm" style={{ color: "var(--ivory)" }}>
          {linkState === "loading" ? "Preparing your link…" : "Save your album link"}
        </span>
      </button>
      {link && (
        <p className="text-xs px-2 break-all" style={{ color: "var(--ink-muted)" }}>
          {link}
        </p>
      )}
      {linkState === "error" && (
        <p className="text-xs px-2" style={{ color: "var(--danger)" }}>
          Couldn&rsquo;t reach the director yet — try again in a moment.
        </p>
      )}

      {!confirmingDelete ? (
        <button
          type="button"
          onClick={() => setConfirmingDelete(true)}
          className="w-full flex items-center gap-3 p-4 rounded-[var(--radius-card)] text-left"
          style={{ border: "1px solid rgb(192 57 43 / 0.4)", background: "var(--bg-1)" }}
        >
          <span aria-hidden>🗑️</span>
          <span className="flex-1 text-sm" style={{ color: "var(--danger)" }}>
            Delete my data
          </span>
        </button>
      ) : (
        <div
          className="p-4 rounded-[var(--radius-card)]"
          style={{ border: "1px solid rgb(192 57 43 / 0.4)", background: "var(--bg-1)" }}
        >
          <p className="text-sm mb-3" style={{ color: "var(--ivory)" }}>
            This removes your face data, your album, and your reactions. Your own uploaded photos
            stay only in the host&rsquo;s archive, no longer linked to you. This can&rsquo;t be
            undone.
          </p>
          {deleteState === "error" && (
            <p className="text-xs mb-3" style={{ color: "var(--danger)" }}>
              Couldn&rsquo;t reach the director yet — try again in a moment.
            </p>
          )}
          <div className="flex gap-3">
            <button
              type="button"
              onClick={() => setConfirmingDelete(false)}
              className="flex-1 py-2 rounded-[var(--radius-pill)] text-sm"
              style={{ color: "var(--ink-muted)" }}
            >
              Cancel
            </button>
            <button
              type="button"
              disabled={deleteState === "loading"}
              onClick={() => void onConfirmDelete()}
              className="flex-1 py-2 rounded-[var(--radius-pill)] text-sm font-medium disabled:opacity-50"
              style={{ background: "var(--danger)", color: "var(--ivory)" }}
            >
              {deleteState === "loading" ? "Deleting…" : "Yes, delete everything"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
