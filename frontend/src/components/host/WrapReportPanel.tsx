"use client";

import { useEffect, useState } from "react";
import {
  Award,
  AlertCircle,
  Trophy,
  User,
  Film,
  Download,
  RefreshCw,
} from "lucide-react";
import { ApiError } from "@/lib/api";
import { commissionReel, reelVideoPath } from "@/lib/hostApi";
import type { WrapReport } from "@/lib/hostTypes";
import { listenReel } from "@/lib/firestore";
import { useAuthedBlobUrl } from "@/lib/useAuthedImage";
import { MediaImg } from "@/lib/MediaImg";
import type { ReelDoc } from "@/lib/types";

/** The whole-event recap film block — status walks `directing → composing → rendering →
 * published` (or `failed`/`unpublished`/`superseded`), same lifecycle `ReelSlot` shows on the
 * kiosk, but read for the host console instead of a premiere takeover.
 *
 * Unlike `ReelSlot`, this always fetches the video through `useAuthedBlobUrl` rather than
 * branching on the event's access mode: a host previewing an unpublished (or failed) reel needs
 * the host bearer regardless of access mode, and sending it for an already-published reel on an
 * open event costs nothing extra — `api/reels.py::reel_video` only inspects the token on the
 * branches that need one. That same already-fetched blob is what backs the "Download film" link
 * below, which is why the panel never calls the `?download=1` variant: a `download` attribute on
 * an anchor forces the browser's save dialog regardless of the blob's own content-disposition, so
 * one authed fetch covers both playback and download, on an open *or* invite-only event, with no
 * second request.
 */
function RecapFilmBody({
  eventId,
  reelId,
  reel,
}: {
  eventId: string;
  reelId: string;
  reel: ReelDoc | null | undefined;
}) {
  const published = reel?.status === "published";
  const videoSrc = useAuthedBlobUrl(published ? reelVideoPath(eventId, reelId) : null);

  if (reel?.status === "failed") {
    return (
      <p className="text-xs text-[var(--warn)] leading-relaxed">
        The recap couldn&rsquo;t be produced this time &mdash; nothing about your photos was lost,
        only the film. Try &ldquo;Regenerate recap&rdquo; above.
      </p>
    );
  }

  if (reel?.status === "unpublished") {
    return (
      <p className="text-xs text-[var(--ink-muted)] leading-relaxed">
        This recap was withdrawn &mdash; someone who appears in it asked not to be shown, and the
        consent interlock pulled it rather than leave it up.
      </p>
    );
  }

  if (reel?.status === "superseded") {
    return (
      <p className="text-xs text-[var(--ink-muted)] leading-relaxed">
        This cut was replaced by a newer one.
      </p>
    );
  }

  if (!published) {
    return (
      <div className="space-y-2">
        <div className="h-40 rounded-xl skeleton-shimmer bg-white/5" />
        <p className="text-xs text-[var(--ink-muted)]">
          {reel?.status === "rendering"
            ? `Rendering the film — ${reel.progress ?? 0}%`
            : "Cutting the film…"}
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {videoSrc ? (
        // eslint-disable-next-line jsx-a11y/media-has-caption
        <video src={videoSrc} controls playsInline className="w-full rounded-xl bg-black" />
      ) : (
        <div className="h-40 rounded-xl skeleton-shimmer bg-white/5" />
      )}
      {videoSrc && (
        <a
          href={videoSrc}
          download={`showrunner-recap-${reelId}.mp4`}
          className="btn-secondary inline-flex items-center gap-1.5 text-[11px] px-3.5 py-1.5 rounded-full font-medium"
        >
          <Download className="w-3.5 h-3.5" />
          <span>Download film</span>
        </a>
      )}
    </div>
  );
}

function RecapFilmBlock({ eventId, recapReelId }: { eventId: string; recapReelId: string | null }) {
  // Starts from the report's own `recapReelId`, but "Regenerate recap" swaps it for the new
  // commission's id immediately rather than waiting on the host event doc's next snapshot — the
  // button's own response already names the reel to listen on.
  const [reelId, setReelId] = useState(recapReelId);
  useEffect(() => setReelId(recapReelId), [recapReelId]);

  const [reel, setReel] = useState<ReelDoc | null | undefined>(undefined);
  useEffect(() => {
    if (!reelId) {
      setReel(undefined);
      return;
    }
    setReel(undefined);
    return listenReel(eventId, reelId, setReel, () => setReel(null));
  }, [eventId, reelId]);

  const [regenBusy, setRegenBusy] = useState(false);
  const [regenError, setRegenError] = useState<string | null>(null);

  async function regenerate() {
    setRegenBusy(true);
    setRegenError(null);
    try {
      const res = await commissionReel(eventId, { persona: "event_recap" });
      setReelId(res.reelId);
    } catch (err) {
      setRegenError(
        err instanceof ApiError ? err.message : "That didn't go through. Nothing changed — try again."
      );
    } finally {
      setRegenBusy(false);
    }
  }

  if (!reelId) return null;

  return (
    <div className="mb-6 p-5 rounded-2xl bg-white/5 border border-white/10">
      <div className="flex items-center justify-between gap-3 mb-3">
        <div className="flex items-center gap-2">
          <Film className="w-4 h-4 text-[var(--accent)]" />
          <p className="text-xs font-semibold uppercase tracking-wider text-[var(--ivory)]">
            The Event Recap
          </p>
        </div>
        <button
          type="button"
          onClick={() => void regenerate()}
          disabled={regenBusy}
          className="btn-secondary text-[11px] px-3.5 py-1.5 rounded-full flex items-center gap-1.5 font-medium disabled:opacity-40"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${regenBusy ? "animate-spin" : ""}`} />
          <span>{regenBusy ? "Commissioning…" : "Regenerate recap"}</span>
        </button>
      </div>

      {regenError && <p className="text-xs text-[var(--danger)] mb-3 leading-relaxed">{regenError}</p>}

      <RecapFilmBody eventId={eventId} reelId={reelId} reel={reel} />
    </div>
  );
}

export function WrapReportPanel({ report, eventId }: { report: WrapReport; eventId: string }) {
  return (
    <section className="mb-10 glass-card p-6 rounded-3xl border border-white/10 shadow-2xl animate-fadeIn">
      <div className="flex items-center gap-2 mb-4">
        <Award className="w-5 h-5 text-[var(--accent)]" />
        <h3 className="font-[family-name:var(--font-display)] text-xl font-medium text-[var(--ivory)]">
          Event Wrap-Up Synthesis
        </h3>
      </div>

      <div className="rounded-2xl p-5 mb-5 bg-[var(--gold-500)]/10 border border-[var(--gold-500)]/20 shadow-md">
        <h4 className="font-[family-name:var(--font-display)] text-2xl font-semibold text-[var(--gold-300)] mb-2">
          {report.headline}
        </h4>
        <div className="flex flex-wrap gap-4 text-xs font-mono text-[var(--ink-muted)]">
          <span>{report.totalPhotos} Ingested Photos</span>
          <span>•</span>
          <span>{report.totalReels} Generated Reels</span>
          <span>•</span>
          <span>{report.totalPhotographers} Photographers</span>
        </div>
      </div>

      <RecapFilmBlock eventId={eventId} recapReelId={report.recapReelId} />

      {report.perStage.length > 0 && (
        <div className="space-y-2.5 mb-6">
          <p className="text-xs font-semibold uppercase tracking-wider text-[var(--ivory)] mb-2">
            Coverage Per Stage Phase
          </p>
          {report.perStage.map((row) => (
            <div key={row.stageId} className="p-3 rounded-xl bg-white/5 border border-white/5">
              <div className="flex items-center justify-between text-xs gap-3">
                <span className="font-medium text-[var(--ivory)]">
                  {row.dayLabel ? `${row.dayLabel} · ${row.label}` : row.label}
                </span>
                <span className="font-mono tabular-nums text-[var(--ink-muted)] shrink-0">
                  {row.photoCount} photos · {row.highlightCount} highlights · avg {row.meanAesthetic.toFixed(2)} score
                </span>
              </div>
              {row.bestMediaIds.length > 0 && (
                <div className="flex gap-1.5 mt-2.5">
                  {row.bestMediaIds.slice(0, 3).map((mediaId) => (
                    <MediaImg
                      key={mediaId}
                      eventId={eventId}
                      mediaId={mediaId}
                      variant="thumb"
                      forceAuthed
                      alt=""
                      className="w-12 h-12 rounded-lg object-cover border border-white/10"
                      fallback={
                        <div className="w-12 h-12 rounded-lg skeleton-shimmer bg-white/5 border border-white/10" />
                      }
                    />
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {report.honestGaps.length > 0 && (
        <div className="mb-6 p-4 rounded-2xl bg-[var(--warn)]/10 border border-[var(--warn)]/20">
          <div className="flex items-center gap-1.5 text-xs font-semibold text-[var(--warn)] mb-2">
            <AlertCircle className="w-4 h-4" />
            <span>Honest Unfilled Gaps (Audited Reality)</span>
          </div>
          <ul className="text-xs space-y-2 text-[var(--ink-muted)]">
            {report.honestGaps.map((g) => (
              <li key={`${g.stageId}-${g.momentId}`}>
                <p>
                  • No verified photos captured for &ldquo;{g.momentLabel}&rdquo; during {g.stageLabel}
                </p>
                {g.detail && <p className="text-[var(--ink-faint)] pl-3 mt-0.5 leading-relaxed">{g.detail}</p>}
              </li>
            ))}
          </ul>
        </div>
      )}

      {report.topContributors.length > 0 && (
        <div>
          <p className="text-xs font-semibold uppercase tracking-wider text-[var(--ivory)] mb-3 flex items-center gap-1.5">
            <Trophy className="w-4 h-4 text-[var(--accent)]" />
            <span>Top Photo Contributors</span>
          </p>
          <div className="space-y-2">
            {report.topContributors.map((c, i) => (
              <div key={c.uid} className="flex items-center gap-3 p-2.5 rounded-xl bg-white/5 text-xs">
                <span className="font-mono w-5 text-right text-[var(--gold-300)] font-bold">
                  {i + 1}
                </span>
                <div className="flex-1 flex items-center gap-2 text-[var(--ivory)] font-medium">
                  <User className="w-3.5 h-3.5 text-[var(--ink-muted)]" />
                  <span>{c.displayName ?? "Guest Contributor"}</span>
                </div>
                <span className="font-mono font-semibold tabular-nums text-[var(--accent)]">
                  {c.points} pts
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}
