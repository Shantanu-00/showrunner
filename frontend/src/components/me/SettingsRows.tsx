"use client";

import { useState } from "react";
import { Link2, Trash2, Check, Copy, AlertTriangle } from "lucide-react";
import { ApiError, createClaimLink, deleteMyData } from "@/lib/api";
import { PushOptIn } from "./PushOptIn";
import { RecapCard } from "./RecapCard";

export function SettingsRows({
  eventId,
  onDeleted,
}: {
  eventId: string;
  onDeleted: () => void;
}) {
  const [linkState, setLinkState] = useState<"idle" | "loading" | "error">("idle");
  const [link, setLink] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
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

  function onCopyLink() {
    if (!link) return;
    navigator.clipboard.writeText(link).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
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
    <div className="px-4 mt-8 space-y-3 pb-8">
      {/* The film first: it is the thing a guest actually came back for once the event is over, and
       * it renders nothing at all until a recap is published (`RecapCard`). */}
      <RecapCard eventId={eventId} />
      <PushOptIn eventId={eventId} />

      <div className="rounded-2xl glass-card p-4 border border-white/10">
        <button
          type="button"
          onClick={() => void onSaveLink()}
          className="w-full flex items-center gap-3 text-left"
        >
          <div className="p-2 rounded-xl bg-[var(--gold-500)]/15 text-[var(--accent)]">
            <Link2 className="w-5 h-5" />
          </div>
          <div className="flex-1">
            <h4 className="text-sm font-semibold text-[var(--ivory)]">
              Save Your Personal Album Link
            </h4>
            <p className="text-xs text-[var(--ink-muted)]">
              {linkState === "loading"
                ? "Generating secure access token…"
                : "Bookmark or share private access across your devices."}
            </p>
          </div>
        </button>

        {link && (
          <div className="mt-3 pt-3 border-t border-white/10 flex items-center gap-2">
            <input
              type="text"
              readOnly
              value={link}
              className="flex-1 text-xs font-mono p-2 rounded-lg bg-black/50 border border-white/10 text-[var(--gold-300)] truncate"
            />
            <button
              type="button"
              onClick={onCopyLink}
              className="px-3 py-2 rounded-lg bg-white/10 hover:bg-white/20 text-xs font-medium text-white flex items-center gap-1 shrink-0"
            >
              {copied ? <Check className="w-3.5 h-3.5 text-[var(--ok)]" /> : <Copy className="w-3.5 h-3.5" />}
              <span>{copied ? "Copied" : "Copy"}</span>
            </button>
          </div>
        )}

        {linkState === "error" && (
          <p className="text-xs text-[var(--danger)] mt-2">
            Failed to connect to host authority — please try again.
          </p>
        )}
      </div>

      {!confirmingDelete ? (
        <button
          type="button"
          onClick={() => setConfirmingDelete(true)}
          className="w-full flex items-center gap-3 p-4 rounded-2xl glass-card border border-[var(--danger)]/30 text-left hover:border-[var(--danger)] transition-all"
        >
          <div className="p-2 rounded-xl bg-[var(--danger)]/15 text-[var(--danger)]">
            <Trash2 className="w-5 h-5" />
          </div>
          <div className="flex-1">
            <h4 className="text-sm font-semibold text-[var(--danger)]">
              Delete My Biometrics & Data
            </h4>
            <p className="text-xs text-[var(--ink-muted)]">
              Irrevocably erase your face index and private album.
            </p>
          </div>
        </button>
      ) : (
        <div className="p-5 rounded-2xl glass-card border-2 border-[var(--danger)]/50 bg-black/60">
          <div className="flex items-center gap-2 text-[var(--danger)] mb-2">
            <AlertTriangle className="w-5 h-5" />
            <h4 className="text-sm font-bold">Confirm Permanent Deletion</h4>
          </div>
          <p className="text-xs text-[var(--ink-muted)] leading-relaxed mb-4">
            This permanently deletes your face embeddings, photo index links, and taste reactions. This action cannot be undone.
          </p>
          {deleteState === "error" && (
            <p className="text-xs text-[var(--danger)] mb-3">
              Server request failed — please retry.
            </p>
          )}
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => setConfirmingDelete(false)}
              className="flex-1 py-2.5 rounded-full btn-secondary text-xs font-medium"
            >
              Cancel
            </button>
            <button
              type="button"
              disabled={deleteState === "loading"}
              onClick={() => void onConfirmDelete()}
              className="flex-1 py-2.5 rounded-full bg-[var(--danger)] text-white text-xs font-bold shadow-lg hover:brightness-110 disabled:opacity-50"
            >
              {deleteState === "loading" ? "Erasing…" : "Delete Everything"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
