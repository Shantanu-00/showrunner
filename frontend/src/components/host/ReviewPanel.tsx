"use client";

import { useCallback, useEffect, useState } from "react";
import {
  ShieldQuestion,
  RefreshCw,
  Eye,
  Lock,
  Ban,
  Check,
  Sparkles,
  Clock,
} from "lucide-react";
import { ApiError } from "@/lib/api";
import { decideMedia, getReviewQueue } from "@/lib/hostApi";
import { MediaImg } from "@/lib/MediaImg";
import type { ReviewQueueItem } from "@/lib/hostTypes";

/**
 * The photo review queue — the escape hatch that makes every conservative default in the pipeline
 * safe to be conservative.
 *
 * Everything the system cannot decide alone lands here: a Guardian refusal, a model that never
 * answered, the deterministic `minor_prominent` rule (a child as the main subject — hosts know whose
 * kids are whose, the system does not), and any sensitivity dial the host themselves declared. All of
 * it parks at `pool` — private, still in the albums of the people in it, on no public surface — and
 * waits. Which is exactly right, but only while somebody can see the queue: a default that nothing
 * can clear is not caution, it is a silent suppression.
 *
 * Two things this panel deliberately does not do:
 *
 * - **It never writes `visibility`.** A decision writes `guardian.hostDecision` and
 *   `recompute_visibility` derives exposure from it, so the host's call is an *input* to the same one
 *   function every other path goes through — not a second writer that could disagree with it.
 * - **It does not offer a release for a blocked photo.** `adult >= LIKELY` at the SafeSearch gate is
 *   the one verdict in this system a console cannot argue with. The blocked tab exists so a host can
 *   *find* those items, because the only available action needs knowing they are there.
 *
 * Reads on mount and after each decision — no polling. A console that polls a review queue spends
 * money all night watching an empty list.
 */

/* ------------------------------------------------------------------ copy
 * Every reason the Guardian and its gate can emit, in the host's language rather than the schema's.
 * `minor_prominent` reading as "minor_prominent" on a decision surface teaches a host to click
 * through it; reading as a sentence about their own event teaches them to look.
 */
const REASON_COPY: Record<string, string> = {
  // Dignity observations (schemas/guardian_out.py::DignityReason)
  eyes_closed: "Eyes closed or mid-blink",
  mid_bite: "Eating — mouth full",
  wardrobe_risk: "Clothing may have slipped",
  distress_out_of_context: "Someone looks genuinely upset, and the moment doesn't explain it",
  unflattering_angle: "An angle that mocks rather than portrays",
  minor_prominent: "A child is a main subject — only you know whose kids are whose",
  pda_visible: "A kiss or an intimate embrace",
  alcohol_visible: "Alcohol in frame",
  attire_revealing: "More revealing than everyday formal wear",
  // Gate-added reasons (workers/safety/gate.py)
  safesearch_adult: "Explicit content — blocked automatically, and this cannot be lifted here",
  safesearch_racy_or_violence: "The automatic screen flagged this as racy or violent",
  model_proposed_blocked: "The dignity check wanted this blocked; it was held instead",
  model_unavailable: "The dignity check didn't complete, so this was held rather than guessed",
  stage_failed: "A processing stage failed, so this was held rather than guessed",
  ritual_emotion: "Strong emotion here reads as part of the occasion",
  dial_pda_private_only: "Your own setting: keep public affection private",
  dial_alcohol_private_only: "Your own setting: keep alcohol private",
  dial_attire_conservative: "Your own setting: conservative attire",
};

function reasonLabel(reason: string): string {
  return REASON_COPY[reason] ?? reason.replace(/_/g, " ");
}

function whenLabel(at?: string | null): string {
  if (!at) return "just now";
  const ms = Date.now() - Date.parse(at);
  if (!Number.isFinite(ms) || ms < 60_000) return "just now";
  const mins = Math.round(ms / 60_000);
  if (mins < 60) return `${mins} min ago`;
  const hours = Math.round(mins / 60);
  return hours < 24 ? `${hours} h ago` : `${Math.round(hours / 24)} d ago`;
}

type Tab = "host_review" | "blocked";

/* ------------------------------------------------------------------ the panel */

export function ReviewPanel({
  eventId,
  onDecided,
}: {
  eventId: string;
  /** Lets the console refresh its KPI badge, which is computed from the same predicate this lists by. */
  onDecided?: () => void;
}) {
  const [tab, setTab] = useState<Tab>("host_review");
  const [items, setItems] = useState<ReviewQueueItem[] | null>(null);
  const [truncated, setTruncated] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const [flash, setFlash] = useState<string | null>(null);

  const load = useCallback(() => {
    setItems(null);
    getReviewQueue(eventId, tab).then(
      (res) => {
        setItems(res.items);
        setTruncated(res.truncated);
        setUnavailable(false);
      },
      (err) => {
        // A deployment whose API predates this endpoint should say so rather than render an empty
        // list, which reads as "nothing to review" — the most misleading thing this panel could do.
        if (err instanceof ApiError && (err.status === 404 || err.status === 405)) {
          setUnavailable(true);
        }
        setItems([]);
      }
    );
  }, [eventId, tab]);

  useEffect(() => {
    load();
  }, [load]);

  async function decide(item: ReviewQueueItem, decision: "public_ok" | "private_only") {
    setBusy(item.mediaId);
    setError(null);
    try {
      const res = await decideMedia(eventId, item.mediaId, decision);
      setFlash(
        decision === "public_ok"
          ? res.visibility === "public"
            ? "Released — it's on the public surfaces now."
            : "Approved. It still isn't public: consent or the quality floor is holding it, which your decision doesn't override."
          : "Kept private — it stays with the people in it and off every public surface."
      );
      setItems((prev) => (prev ?? []).filter((i) => i.mediaId !== item.mediaId));
      onDecided?.();
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "That didn't go through. Nothing changed — try again."
      );
    } finally {
      setBusy(null);
    }
  }

  const count = items?.length ?? 0;

  return (
    <section className="mb-10 glass-card p-6 rounded-3xl border border-white/10 shadow-xl">
      <div className="flex items-start justify-between gap-3 mb-2">
        <div className="flex items-center gap-2">
          <ShieldQuestion className="w-4 h-4 text-[var(--accent)]" />
          <h3 className="font-[family-name:var(--font-display)] text-lg font-medium text-[var(--ivory)]">
            Photos waiting on you
          </h3>
          {count > 0 && (
            <span className="text-[11px] font-mono font-bold tabular-nums px-2 py-0.5 rounded-full bg-[var(--accent)] text-black">
              {count}
              {truncated ? "+" : ""}
            </span>
          )}
        </div>
        <button
          type="button"
          onClick={load}
          aria-label="Refresh the queue"
          className="shrink-0 w-9 h-9 rounded-full text-[var(--ink-muted)] hover:text-[var(--ivory)] hover:bg-white/10 flex items-center justify-center transition-colors"
        >
          <RefreshCw className="w-4 h-4" />
        </button>
      </div>

      <p className="text-xs text-[var(--ink-muted)] mb-4 leading-relaxed">
        Nothing here is public. Each of these was held because the system wasn&rsquo;t willing to guess
        &mdash; they stay with the people in them until you decide.
      </p>

      <div className="flex items-center p-1 rounded-full bg-white/5 border border-white/10 w-fit mb-5">
        {(
          [
            ["host_review", "Waiting on you"],
            ["blocked", "Blocked"],
          ] as Array<[Tab, string]>
        ).map(([value, label]) => (
          <button
            key={value}
            type="button"
            onClick={() => setTab(value)}
            className={`text-xs px-3.5 py-1.5 rounded-full transition-all font-medium ${
              tab === value
                ? "bg-[var(--accent)] text-black font-semibold shadow-md"
                : "text-[var(--ink-muted)] hover:text-[var(--ivory)]"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {unavailable && (
        <p className="text-xs text-[var(--warn)] mb-4 leading-relaxed">
          This deployment&rsquo;s API doesn&rsquo;t serve the review queue yet, so this list is
          incomplete. Nothing has been released &mdash; held photos are still held.
        </p>
      )}

      {tab === "blocked" && (
        <p className="text-xs text-[var(--warn)] mb-4 leading-relaxed flex items-start gap-1.5">
          <Ban className="w-3.5 h-3.5 mt-0.5 shrink-0" />
          <span>
            These were stopped by the automatic explicit-content screen. That verdict can&rsquo;t be
            lifted from a console, by design &mdash; they&rsquo;re visible only to whoever uploaded
            them. This tab exists so you know they&rsquo;re there.
          </span>
        </p>
      )}

      {items === null && (
        <div className="space-y-3">
          {[0, 1].map((i) => (
            <div key={i} className="h-32 rounded-2xl skeleton-shimmer bg-white/5" />
          ))}
          <p className="text-xs text-[var(--ink-muted)]">Fetching the queue&hellip;</p>
        </div>
      )}

      {items !== null && count === 0 && (
        <p className="text-xs text-[var(--ink-muted)] p-5 rounded-2xl bg-white/[0.03] border border-white/5 leading-relaxed">
          {tab === "host_review"
            ? "Nothing waiting. Anything the system can't judge on its own lands here — a child as the main subject, a moment it can't read, or one of your own sensitivity settings."
            : "Nothing blocked at this event."}
        </p>
      )}

      {flash && (
        <p className="text-xs text-[var(--ok)] mb-4 flex items-start gap-1.5">
          <Check className="w-3.5 h-3.5 mt-0.5 stroke-[3] shrink-0" />
          <span>{flash}</span>
        </p>
      )}

      {error && <p className="text-xs text-[var(--danger)] mb-4 leading-relaxed">{error}</p>}

      <div className="space-y-3">
        {(items ?? []).map((item) => (
          <ReviewCard
            key={item.mediaId}
            eventId={eventId}
            item={item}
            readOnly={tab === "blocked"}
            busy={busy === item.mediaId}
            onRelease={() => void decide(item, "public_ok")}
            onKeepPrivate={() => void decide(item, "private_only")}
          />
        ))}
      </div>

      {truncated && (
        <p className="text-[11px] text-[var(--ink-faint)] mt-4 leading-relaxed">
          More than this page holds. Clear some and refresh &mdash; the newest are shown first.
        </p>
      )}
    </section>
  );
}

function ReviewCard({
  eventId,
  item,
  readOnly,
  busy,
  onRelease,
  onKeepPrivate,
}: {
  eventId: string;
  item: ReviewQueueItem;
  readOnly: boolean;
  busy: boolean;
  onRelease: () => void;
  onKeepPrivate: () => void;
}) {
  // A held photo is never `public`, so its bytes always need the host's token on either kind of event.
  const isChild = item.reasons.includes("minor_prominent");

  return (
    <article
      className={`p-4 rounded-2xl bg-white/[0.03] border ${
        isChild || readOnly ? "border-[var(--warn)]/30" : "border-white/10"
      }`}
      aria-label={`Photo ${item.mediaId} awaiting review`}
    >
      <div className="flex gap-4">
        <div className="shrink-0">
          <MediaImg
            eventId={eventId}
            mediaId={item.mediaId}
            variant="thumb"
            forceAuthed
            alt={item.caption ?? "A photo awaiting your decision"}
            className="w-28 h-28 rounded-xl object-cover border border-white/10"
            fallback={
              <div className="w-28 h-28 rounded-xl skeleton-shimmer bg-white/5 border border-white/10" />
            }
          />
          <p className="text-[10px] text-[var(--ink-faint)] font-mono mt-1 text-center tabular-nums">
            {item.visibility ?? "pool"}
          </p>
        </div>

        <div className="min-w-0 flex-1">
          {item.caption ? (
            <p className="text-sm text-[var(--ivory)] leading-snug mb-2 line-clamp-2">
              {item.caption}
            </p>
          ) : (
            <p className="text-sm text-[var(--ink-muted)] italic mb-2">No caption</p>
          )}

          <ul className="space-y-1 mb-2">
            {item.reasons.length === 0 ? (
              <li className="text-xs text-[var(--ink-muted)]">
                Held with no specific reason recorded.
              </li>
            ) : (
              item.reasons.map((r) => (
                <li
                  key={r}
                  className={`text-xs leading-snug flex items-start gap-1.5 ${
                    r === "minor_prominent" || r.startsWith("safesearch")
                      ? "text-[var(--warn)]"
                      : "text-[var(--ink-muted)]"
                  }`}
                >
                  <span aria-hidden="true">&middot;</span>
                  <span>{reasonLabel(r)}</span>
                </li>
              ))
            )}
          </ul>

          {item.note && (
            <p className="text-xs text-[var(--ink-muted)] leading-relaxed mb-2 pl-2 border-l border-[var(--gold-500)]/30">
              {item.note}
            </p>
          )}

          {item.offTopicNote && (
            <p className="text-[11px] text-[var(--ink-faint)] leading-relaxed mb-2 flex items-start gap-1.5">
              <Sparkles className="w-3 h-3 mt-0.5 shrink-0" />
              <span>{item.offTopicNote}</span>
            </p>
          )}

          <p className="text-[11px] text-[var(--ink-faint)] font-mono tabular-nums flex items-center gap-1.5">
            <Clock className="w-3 h-3" />
            <span>{whenLabel(item.uploadedAt)}</span>
            <span>&middot;</span>
            <span>quality {item.aestheticScore.toFixed(2)}</span>
            {item.ritualEmotion && (
              <>
                <span>&middot;</span>
                <span className="text-[var(--gold-300)]">part of the occasion</span>
              </>
            )}
          </p>
        </div>
      </div>

      {!readOnly && (
        <div className="flex flex-col sm:flex-row gap-2 mt-4">
          <button
            type="button"
            disabled={busy}
            onClick={onRelease}
            className="btn-primary flex-1 py-3 rounded-full text-xs font-semibold flex items-center justify-center gap-2 disabled:opacity-40"
          >
            <Eye className="w-4 h-4" />
            <span>{busy ? "Working…" : "Fine to show"}</span>
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={onKeepPrivate}
            className="btn-secondary flex-1 py-3 rounded-full text-xs font-semibold flex items-center justify-center gap-2 disabled:opacity-40"
          >
            <Lock className="w-4 h-4" />
            <span>Keep private</span>
          </button>
        </div>
      )}

      {isChild && !readOnly && (
        <p className="text-[11px] text-[var(--warn)] mt-3 leading-relaxed">
          A child is a main subject here. This is always routed to you &mdash; the system never makes
          that call itself.
        </p>
      )}
    </article>
  );
}
