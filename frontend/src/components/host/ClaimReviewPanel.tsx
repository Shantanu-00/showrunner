"use client";

import { useCallback, useEffect, useState } from "react";
import {
  UserCheck,
  RefreshCw,
  ShieldAlert,
  Check,
  X,
  Undo2,
  Sparkles,
  Smartphone,
  Link2,
} from "lucide-react";
import { authedJson, ApiError } from "@/lib/api";
import { resolveSignedUrl } from "@/lib/mediaUrls";

/* ------------------------------------------------------------------ wire shapes
 * Mirrors `backend/schemas/identity.py`'s review-queue models. Spelled out here rather than in
 * `lib/hostTypes.ts` because that file belongs to another workstream this session; they should be
 * folded in afterwards.
 *
 * GET  /v1/events/{eventId}/claims?status=held            → { claims: ClaimReviewCard[] }
 * POST /v1/events/{eventId}/claims/{claimId}/review       { decision: "approve" | "deny" }
 * POST /v1/events/{eventId}/claims/{claimId}/reverse      (no body)
 * GET  /v1/events/{eventId}/claims/{claimId}/selfie       302 → signed URL, host-gated
 */

type ClaimMethod = "enroll" | "reclaim" | "magic_link";
type ClaimStatus = "applied" | "held" | "approved" | "denied" | "reversed";
type ClaimHoldReason = "claim_size" | "protected_person" | "ambiguous_match" | "host_approval";

interface ClaimReviewExemplar {
  mediaId: string;
  faceId: string;
  similarity: number;
  thumbUrl?: string | null;
}

interface ClaimReviewCard {
  claimId: string;
  method: ClaimMethod;
  status: ClaimStatus;
  holdReason?: ClaimHoldReason | null;
  displayName?: string | null;
  faceCount: number;
  topSimilarity: number;
  at?: string | null;
  createdPerson: boolean;
  selfieUrl?: string | null;
  exemplars: ClaimReviewExemplar[];
}

function listClaims(eventId: string, status: ClaimStatus): Promise<{ claims: ClaimReviewCard[] }> {
  return authedJson(`/v1/events/${eventId}/claims?status=${status}`, { method: "GET" });
}

function reviewClaim(eventId: string, claimId: string, decision: "approve" | "deny"): Promise<unknown> {
  return authedJson(`/v1/events/${eventId}/claims/${claimId}/review`, {
    method: "POST",
    body: JSON.stringify({ decision }),
  });
}

function reverseClaim(eventId: string, claimId: string): Promise<unknown> {
  return authedJson(`/v1/events/${eventId}/claims/${claimId}/reverse`, {
    method: "POST",
    body: "{}",
  });
}

/* ------------------------------------------------------------------ authed thumbnails
 * Neither the selfie nor an exemplar thumb is a URL an `<img>` can load as stored: every bucket has
 * public access prevention, so both arrive as host-gated API paths.
 *
 * This used to *fetch* those paths with the host's token and follow the 302 to a blob — and it never
 * once worked in the browser. A request carrying `Authorization` is preflighted, the preflight cannot
 * be redirected onto `storage.googleapis.com`, and a bucket CORS policy cannot allow a request header
 * anyway: every selfie tile was a permanent shimmer with a CORS error behind it, which made the review
 * queue undecidable — the host was asked "is this the same person?" about a photo they could not see.
 * `?json=1` returns the signed URL instead (`api/identity.py`, `api/media.py`), and a plain `<img src>`
 * needs no header at all. Uses `lib/mediaUrls.ts` so the resolution is cached like every other one.
 */
function useSignedSrc(path: string | null | undefined): string | null {
  const [src, setSrc] = useState<string | null>(null);

  useEffect(() => {
    if (!path) {
      setSrc(null);
      return;
    }
    let cancelled = false;
    void resolveSignedUrl(path).then((url) => {
      if (!cancelled) setSrc(url);
    });
    return () => {
      cancelled = true;
    };
  }, [path]);

  return src;
}

function AuthedTile({
  path,
  alt,
  className,
}: {
  path: string | null | undefined;
  alt: string;
  className: string;
}) {
  const src = useSignedSrc(path);
  if (!src) {
    return (
      <div
        aria-label={alt}
        className={`${className} skeleton-shimmer bg-white/5 border border-white/10`}
      />
    );
  }
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={src}
      alt={alt}
      decoding="async"
      className={`${className} object-cover border border-white/10`}
    />
  );
}

/* ------------------------------------------------------------------ copy */

const REASON: Record<ClaimHoldReason, { label: string; note: string; tone: "info" | "warn" }> = {
  host_approval: {
    label: "Routine",
    note: "No warning signs — just confirm the selfie is the same person as the photos.",
    tone: "info",
  },
  claim_size: {
    label: "Large album",
    note: "This one claim would hand over a lot of photos at once. Worth a second look.",
    tone: "warn",
  },
  protected_person: {
    label: "Someone you named",
    note: "The selfie matched a person you enrolled yourself. Never granted without you.",
    tone: "warn",
  },
  ambiguous_match: {
    label: "Two people look alike",
    note: "The top two matches were too close to separate — siblings, or a very similar face.",
    tone: "warn",
  },
};

const METHOD: Record<ClaimMethod, { label: string; icon: React.ElementType }> = {
  enroll: { label: "Took a selfie", icon: Sparkles },
  reclaim: { label: "New device", icon: Smartphone },
  magic_link: { label: "Album link", icon: Link2 },
};

function whenLabel(at?: string | null): string {
  if (!at) return "just now";
  const ms = Date.now() - Date.parse(at);
  if (!Number.isFinite(ms) || ms < 60_000) return "just now";
  const mins = Math.round(ms / 60_000);
  if (mins < 60) return `${mins} min ago`;
  const hours = Math.round(mins / 60);
  return hours < 24 ? `${hours} h ago` : `${Math.round(hours / 24)} d ago`;
}

/* ------------------------------------------------------------------ the panel */

/**
 * The pending-album queue — spec 02 §3.1's "five-second visual check", made operable.
 *
 * Every enrollment and every re-claim is now held for the host, because a face match on its own was
 * granting strangers other people's private albums. That made this screen load-bearing rather than
 * nice-to-have: without it a correctly-held claim is unresolvable by any means, so the guard that
 * fires is a guard that silently drops the guest.
 *
 * Reads once per mount and once per decision — no polling (a host console that polls a review queue
 * spends money all night to watch an empty list).
 */
export function ClaimReviewPanel({ eventId }: { eventId: string }) {
  const [held, setHeld] = useState<ClaimReviewCard[] | null>(null);
  const [decided, setDecided] = useState<ClaimReviewCard[] | null>(null);
  const [showDecided, setShowDecided] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const [flash, setFlash] = useState<{ claimId: string; text: string } | null>(null);

  const loadHeld = useCallback(() => {
    listClaims(eventId, "held").then(
      (res) => {
        setHeld(res.claims);
        setUnavailable(false);
      },
      (err) => {
        // A deployment whose API predates the queue endpoint should say so plainly rather than
        // render an empty list that reads as "nothing to review".
        if (err instanceof ApiError && (err.status === 404 || err.status === 405)) setUnavailable(true);
        setHeld([]);
      }
    );
  }, [eventId]);

  const loadDecided = useCallback(() => {
    listClaims(eventId, "approved").then(
      (res) => setDecided(res.claims),
      () => setDecided([])
    );
  }, [eventId]);

  useEffect(() => {
    loadHeld();
  }, [loadHeld]);

  useEffect(() => {
    if (showDecided) loadDecided();
  }, [showDecided, loadDecided]);

  async function decide(card: ClaimReviewCard, decision: "approve" | "deny") {
    setBusy(card.claimId);
    setError(null);
    try {
      await reviewClaim(eventId, card.claimId, decision);
      setFlash({
        claimId: card.claimId,
        text:
          decision === "approve"
            ? `${card.displayName || "This guest"} can see their album now.`
            : "Denied — nothing was linked, and no album was granted.",
      });
      setHeld((prev) => (prev ?? []).filter((c) => c.claimId !== card.claimId));
      if (showDecided) loadDecided();
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

  async function undo(card: ClaimReviewCard) {
    setBusy(card.claimId);
    setError(null);
    try {
      await reverseClaim(eventId, card.claimId);
      setFlash({ claimId: card.claimId, text: "Reversed — that device no longer has the album." });
      setDecided((prev) => (prev ?? []).filter((c) => c.claimId !== card.claimId));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't reverse that one.");
    } finally {
      setBusy(null);
    }
  }

  const count = held?.length ?? 0;

  return (
    <section className="mb-10 glass-card p-6 rounded-3xl border border-white/10 shadow-xl">
      <div className="flex items-start justify-between gap-3 mb-2">
        <div className="flex items-center gap-2">
          <UserCheck className="w-4 h-4 text-[var(--accent)]" />
          <h3 className="font-[family-name:var(--font-display)] text-lg font-medium text-[var(--ivory)]">
            Albums waiting on you
          </h3>
          {count > 0 && (
            <span className="text-[11px] font-mono font-bold tabular-nums px-2 py-0.5 rounded-full bg-[var(--accent)] text-black">
              {count}
            </span>
          )}
        </div>
        <button
          type="button"
          onClick={() => {
            loadHeld();
            if (showDecided) loadDecided();
          }}
          aria-label="Refresh the queue"
          className="shrink-0 w-9 h-9 rounded-full text-[var(--ink-muted)] hover:text-[var(--ivory)] hover:bg-white/10 flex items-center justify-center transition-colors"
        >
          <RefreshCw className="w-4 h-4" />
        </button>
      </div>

      <p className="text-xs text-[var(--ink-muted)] mb-5 leading-relaxed">
        Nobody gets a private album until you say it&rsquo;s them. Compare the selfie on the left with
        the photos beside it — if it&rsquo;s the same person, approve.
      </p>

      {unavailable && (
        <p className="text-xs text-[var(--warn)] mb-4 leading-relaxed">
          This deployment&rsquo;s API doesn&rsquo;t serve the review queue yet, so the list below may be
          incomplete. Requests are still being held safely — nothing has been granted.
        </p>
      )}

      {held === null && (
        <div className="space-y-3">
          {[0, 1].map((i) => (
            <div key={i} className="h-28 rounded-2xl skeleton-shimmer bg-white/5" />
          ))}
          <p className="text-xs text-[var(--ink-muted)]">Fetching the queue…</p>
        </div>
      )}

      {held !== null && count === 0 && (
        <p className="text-xs text-[var(--ink-muted)] p-5 rounded-2xl bg-white/[0.03] border border-white/5 leading-relaxed">
          Nothing waiting. Every guest who asks for their own album lands here first, so this is the
          screen to come back to when someone says they can&rsquo;t find their photos.
        </p>
      )}

      {flash && (
        <p className="text-xs text-[var(--ok)] mb-4 flex items-center gap-1.5">
          <Check className="w-3.5 h-3.5 stroke-[3]" />
          <span>{flash.text}</span>
        </p>
      )}

      {error && <p className="text-xs text-[var(--danger)] mb-4 leading-relaxed">{error}</p>}

      <div className="space-y-3">
        {(held ?? []).map((card) => (
          <ClaimCard
            key={card.claimId}
            eventId={eventId}
            card={card}
            busy={busy === card.claimId}
            onApprove={() => void decide(card, "approve")}
            onDeny={() => void decide(card, "deny")}
          />
        ))}
      </div>

      <div className="mt-5 pt-4 border-t border-white/5">
        <button
          type="button"
          onClick={() => setShowDecided((v) => !v)}
          className="text-xs font-semibold text-[var(--accent)] hover:underline"
        >
          {showDecided ? "Hide approved albums" : "Approved one by mistake?"}
        </button>

        {showDecided && (
          <div className="mt-3 space-y-2">
            {decided === null && (
              <div className="h-14 rounded-xl skeleton-shimmer bg-white/5" />
            )}
            {decided !== null && decided.length === 0 && (
              <p className="text-xs text-[var(--ink-muted)] leading-relaxed">
                Nothing approved yet on this event.
              </p>
            )}
            {(decided ?? []).map((card) => (
              <div
                key={card.claimId}
                className="flex items-center gap-3 p-3 rounded-xl bg-white/5 border border-white/10"
              >
                <div className="flex-1 min-w-0">
                  <span className="block text-sm font-medium text-[var(--ivory)] truncate">
                    {card.displayName || "Unnamed guest"}
                  </span>
                  <span className="block text-[11px] text-[var(--ink-faint)] font-mono">
                    approved · {whenLabel(card.at)}
                  </span>
                </div>
                <button
                  type="button"
                  disabled={busy === card.claimId}
                  onClick={() => void undo(card)}
                  className="btn-secondary shrink-0 px-3.5 py-2 text-[11px] font-semibold flex items-center gap-1.5 disabled:opacity-40"
                >
                  <Undo2 className="w-3.5 h-3.5" />
                  <span>{busy === card.claimId ? "Reversing…" : "Reverse"}</span>
                </button>
              </div>
            ))}
            <p className="text-[11px] text-[var(--ink-faint)] leading-relaxed">
              Reversing takes the album back off that device. It never deletes anyone&rsquo;s photos —
              deleting is the guest&rsquo;s own decision to make.
            </p>
          </div>
        )}
      </div>
    </section>
  );
}

function ClaimCard({
  eventId,
  card,
  busy,
  onApprove,
  onDeny,
}: {
  eventId: string;
  card: ClaimReviewCard;
  busy: boolean;
  onApprove: () => void;
  onDeny: () => void;
}) {
  const reason = REASON[card.holdReason ?? "host_approval"] ?? REASON.host_approval;
  const method = METHOD[card.method] ?? METHOD.enroll;
  // Spec 02 §3 calls for four exemplars beside the selfie — more turns a five-second check into a
  // reading exercise, and the cluster is already ranked by similarity.
  const exemplars = card.exemplars.slice(0, 4);

  return (
    <article
      className={`p-4 rounded-2xl bg-white/[0.03] border ${
        reason.tone === "warn" ? "border-[var(--warn)]/30" : "border-white/10"
      }`}
      aria-label={`Album request from ${card.displayName || "an unnamed guest"}`}
    >
      <div className="flex items-start justify-between gap-3 mb-3">
        <div className="min-w-0">
          <h4 className="font-[family-name:var(--font-display)] text-base font-medium text-[var(--ivory)] truncate">
            {card.displayName || "Unnamed guest"}
          </h4>
          <div className="flex items-center gap-2 mt-0.5 text-[11px] text-[var(--ink-muted)] font-mono">
            <method.icon className="w-3 h-3" />
            <span>{method.label}</span>
            <span>·</span>
            <span>{whenLabel(card.at)}</span>
          </div>
        </div>
        <span
          className={`shrink-0 text-[10px] font-mono font-bold uppercase tracking-wider px-2.5 py-1 rounded-full border ${
            reason.tone === "warn"
              ? "bg-[var(--warn)]/15 border-[var(--warn)]/30 text-[var(--warn)]"
              : "bg-white/5 border-white/10 text-[var(--ink-muted)]"
          }`}
        >
          {reason.label}
        </span>
      </div>

      <div className="flex gap-3 mb-3 overflow-x-auto pb-1">
        <div className="shrink-0">
          <AuthedTile
            path={card.selfieUrl}
            alt="The selfie this guest submitted"
            className="w-24 h-24 rounded-xl"
          />
          <p className="text-[10px] text-[var(--ink-faint)] font-mono mt-1 text-center">selfie</p>
        </div>

        <div className="w-px self-stretch bg-[var(--gold-500)]/25 shrink-0" aria-hidden="true" />

        {exemplars.length === 0 ? (
          <div className="flex-1 flex items-center min-w-[8rem]">
            <p className="text-xs text-[var(--ink-muted)] leading-relaxed">
              No photos to compare against yet — this would be a brand new album. Approve only if you
              know who this is.
            </p>
          </div>
        ) : (
          <div className="flex gap-2">
            {exemplars.map((ex) => (
              <div key={ex.faceId || ex.mediaId} className="shrink-0">
                <AuthedTile
                  path={ex.thumbUrl}
                  alt="A photo from the album this request would unlock"
                  className="w-24 h-24 rounded-xl"
                />
                <p className="text-[10px] text-[var(--ink-faint)] font-mono mt-1 text-center tabular-nums">
                  {(ex.similarity * 100).toFixed(0)}%
                </p>
              </div>
            ))}
          </div>
        )}
      </div>

      <p className="text-xs text-[var(--ink-muted)] leading-relaxed mb-1">{reason.note}</p>
      <p className="text-[11px] text-[var(--ink-faint)] font-mono tabular-nums mb-4">
        {card.faceCount} photo{card.faceCount === 1 ? "" : "s"} · closest match{" "}
        {(card.topSimilarity * 100).toFixed(0)}%
        {card.createdPerson ? " · new album" : " · joins an existing album"}
      </p>

      <div className="flex flex-col sm:flex-row gap-2">
        <button
          type="button"
          disabled={busy}
          onClick={onApprove}
          className="btn-primary flex-1 py-3 rounded-full text-xs font-semibold flex items-center justify-center gap-2 disabled:opacity-40"
        >
          <Check className="w-4 h-4 stroke-[3]" />
          <span>{busy ? "Working…" : "Approve — it's them"}</span>
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={onDeny}
          className="flex-1 py-3 rounded-full text-xs font-semibold flex items-center justify-center gap-2 bg-[var(--danger)]/12 border border-[var(--danger)]/35 text-[var(--danger)] hover:bg-[var(--danger)]/20 transition-colors disabled:opacity-40"
        >
          <X className="w-4 h-4 stroke-[3]" />
          <span>Deny</span>
        </button>
      </div>

      {reason.tone === "warn" && (
        <p className="flex items-start gap-1.5 text-[11px] text-[var(--warn)] mt-3 leading-relaxed">
          <ShieldAlert className="w-3.5 h-3.5 mt-0.5 shrink-0" />
          <span>
            Approving hands this device every photo in that album, and the ability to remove those
            photos from public view. Deny if you&rsquo;re not sure — they can ask again.
          </span>
        </p>
      )}

      <p className="sr-only">
        Claim {card.claimId} on event {eventId}
      </p>
    </article>
  );
}
