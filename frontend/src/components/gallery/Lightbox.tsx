"use client";

import { useState, type ReactNode } from "react";
import { X, Download, Share2, Clock, Check, Sparkles } from "lucide-react";
import type { MediaDoc } from "@/lib/types";
import { MediaImg } from "@/lib/MediaImg";
import { authedFetch, mediaRenderPath } from "@/lib/api";
import { useHaptics } from "@/lib/useHaptics";

function formatTimestamp(isoStr?: string | null): string {
  if (!isoStr) return "Live Stream";
  try {
    const d = new Date(isoStr);
    return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit", hour12: true });
  } catch {
    return "Event Moment";
  }
}

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
  const isPublic = media.visibility === "public";
  const [downloading, setDownloading] = useState(false);
  const [copied, setCopied] = useState(false);
  const { tapHaptic, successHaptic } = useHaptics();

  const handleClose = () => {
    tapHaptic();
    onClose();
  };

  const handleDownload = async () => {
    if (!variant || downloading) return;
    tapHaptic();
    setDownloading(true);
    try {
      const res = await authedFetch(mediaRenderPath(eventId, media.mediaId, variant), { method: "GET" });
      if (!res.ok) return;
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `showrunner-${media.mediaId}.jpg`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      successHaptic();
    } catch {
      // safe fallback
    } finally {
      setDownloading(false);
    }
  };

  const handleShare = async () => {
    tapHaptic();
    if (typeof window !== "undefined") {
      const shareUrl = window.location.href;
      if (navigator.share) {
        try {
          await navigator.share({
            title: media.curator?.caption || "Showrunner Moment",
            text: "Check out this photo from the event!",
            url: shareUrl,
          });
          successHaptic();
          return;
        } catch {
          // user cancelled
        }
      }
      try {
        await navigator.clipboard.writeText(shareUrl);
        setCopied(true);
        successHaptic();
        setTimeout(() => setCopied(false), 2000);
      } catch {
        // fallback
      }
    }
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Photo Lightbox"
      onClick={handleClose}
      className="fixed inset-0 z-50 flex flex-col justify-between bg-slate-950/95 backdrop-blur-2xl animate-fadeIn transition-opacity select-none"
    >
      {/* Top Header Bar with Timestamp and Close button */}
      <div
        className="flex items-center justify-between p-4 sm:p-6 z-10"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-2 px-3.5 py-1.5 rounded-full glass-pill text-xs font-mono tabular-nums text-[var(--text-secondary)] border border-white/10 shadow-lg">
          <Clock className="w-3.5 h-3.5 text-[var(--accent)]" />
          <span>{formatTimestamp(media.capturedAt || media.createdAt)}</span>
        </div>

        <button
          type="button"
          onClick={handleClose}
          className="p-2.5 rounded-full glass-card hover:bg-white/15 text-[var(--text-secondary)] hover:text-white transition-all cursor-pointer active:scale-95 shadow-lg"
          aria-label="Close photo viewer"
        >
          <X className="w-6 h-6 stroke-[2]" />
        </button>
      </div>

      {/* Main Image Container with Smooth Zoom Entrance */}
      <div
        className="flex-1 flex items-center justify-center px-4 overflow-hidden animate-spring-in"
        onClick={(e) => e.stopPropagation()}
      >
        {variant ? (
          <MediaImg
            eventId={eventId}
            mediaId={media.mediaId}
            variant={variant}
            forceAuthed={!isPublic}
            alt={media.curator?.caption ?? ""}
            className="max-h-[72vh] max-w-full object-contain rounded-2xl sm:rounded-3xl shadow-[0_0_40px_rgba(0,0,0,0.85)] border border-white/10 transition-transform duration-300"
            fallback={<div className="w-full max-w-md h-80 skeleton-shimmer rounded-2xl" />}
          />
        ) : (
          <div className="w-full max-w-md h-80 skeleton-shimmer rounded-2xl" />
        )}
      </div>

      {/* Bottom Floating Bar with Caption & Quick Actions */}
      <div
        className="p-4 sm:p-6 max-w-2xl mx-auto w-full text-center z-10"
        onClick={(e) => e.stopPropagation()}
      >
        {media.curator?.caption && (
          <p className="font-[family-name:var(--font-display)] italic text-base sm:text-lg text-[var(--text-primary)] mb-3 leading-relaxed drop-shadow-md">
            &ldquo;{media.curator.caption}&rdquo;
          </p>
        )}

        {/* Quick Action Bar: 1-Tap Download, Share, plus contextual actions */}
        <div className="flex flex-wrap gap-2.5 justify-center items-center">
          <button
            type="button"
            onClick={() => void handleDownload()}
            disabled={downloading}
            className="flex items-center gap-1.5 text-xs px-4 py-2.5 rounded-full glass-card hover:border-[var(--accent)] text-[var(--text-primary)] font-medium active:scale-95 transition-all cursor-pointer shadow-lg"
          >
            <Download className="w-3.5 h-3.5 text-[var(--accent)]" />
            <span>{downloading ? "Saving…" : "Download"}</span>
          </button>

          <button
            type="button"
            onClick={() => void handleShare()}
            className="flex items-center gap-1.5 text-xs px-4 py-2.5 rounded-full glass-card hover:border-[var(--accent)] text-[var(--text-primary)] font-medium active:scale-95 transition-all cursor-pointer shadow-lg"
          >
            {copied ? (
              <>
                <Check className="w-3.5 h-3.5 text-[var(--emerald-live)]" />
                <span className="text-[var(--emerald-live)]">Link Copied</span>
              </>
            ) : (
              <>
                <Share2 className="w-3.5 h-3.5 text-[var(--accent)]" />
                <span>Share</span>
              </>
            )}
          </button>

          {actions}
        </div>
      </div>
    </div>
  );
}
