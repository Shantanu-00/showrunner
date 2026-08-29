"use client";

import { useCallback, useEffect, useState } from "react";
import {
  DoorOpen,
  Lock,
  Copy,
  Check,
  RefreshCw,
  Tv,
  EyeOff,
  Users,
  Plus,
  KeyRound,
  UserPlus,
  Trash2,
  AlertTriangle,
} from "lucide-react";
import { authedJson, ApiError } from "@/lib/api";
import { createHostLink } from "@/lib/hostApi";
import type { HostEventDoc } from "@/lib/hostTypes";

/* ------------------------------------------------------------------ wire shapes
 * Confirmed against `backend/api/host.py` / `backend/schemas/host.py`:
 *   POST /v1/events/{id}/access        { mode, maxGuests?, confirm? }   → AccessResponse
 *   POST /v1/events/{id}/access/code   (rotate the invite code)          → AccessResponse
 *   POST /v1/events/{id}/access/seats  { maxGuests: number | null }      → AccessResponse
 *   POST /v1/events/{id}/host-links    (mint a co-host link)             → { url, code, expiresAt }
 *
 * Assumed, and degraded gracefully where absent (see the report accompanying this change):
 *   POST /v1/events/{id}/access/kiosk       { kioskPublic }              → AccessResponse
 *   GET  /v1/events/{id}/host-links                                      → { links: HostLinkRow[] }
 *   POST /v1/events/{id}/host-links/{linkId}/revoke                      → { revoked: true }
 *   POST /v1/events/{id}/recovery-code     (mint a replacement)          → { recoveryCode }
 */

type AccessMode = "open" | "invite";

interface AccessResponse {
  eventId: string;
  mode: AccessMode;
  maxGuests?: number | null;
  guestCount: number;
  joinCode?: string | null;
  joinUrl?: string | null;
  codeRotatedAt?: string | null;
  kioskPublic: boolean;
}

interface StoredAccess {
  mode?: AccessMode;
  maxGuests?: number | null;
  codeHash?: string | null;
  codeRotatedAt?: string | null;
  kioskPublic?: boolean;
}

interface HostLinkRow {
  linkId?: string | null;
  /** Present only for a link minted in *this* session. `GET /host-links` cannot return it: only the
   *  sha256 of a code is ever stored, so the plaintext genuinely cannot be reproduced after the fact.
   *  A previously-minted link is therefore listed and revocable, but not re-copyable. */
  url?: string | null;
  grants?: string | null;
  recovery?: boolean | null;
  createdAt?: string | null;
  expiresAt?: string | null;
  revoked?: boolean | null;
  revokedAt?: string | null;
  /** Server-computed "does this still let someone in": neither revoked nor past its expiry. Filter on
   *  this rather than on `revokedAt`, which is blind to expiry. Absent on a locally-minted row, which
   *  is by definition live, hence the `!== false`. */
  active?: boolean | null;
}

const SEAT_STEP = 50;

/** What to show for a link whose plaintext URL we cannot know — every link not minted in this session.
 *  Describes it well enough for the host to decide whether to revoke it: what it grants, and when it
 *  was made. Never the `linkId`, which is a hash and means nothing to a human. */
function linkLabel(l: HostLinkRow): string {
  const kind = l.recovery ? "recovery code" : l.grants === "member" ? "kiosk link" : "co-host link";
  const made = l.createdAt ? new Date(l.createdAt).toLocaleString() : "date unknown";
  return `${kind} · created ${made} · code not recoverable`;
}

function setAccessMode(
  eventId: string,
  body: { mode: AccessMode; maxGuests?: number | null; confirm?: boolean }
): Promise<AccessResponse> {
  return authedJson(`/v1/events/${eventId}/access`, { method: "POST", body: JSON.stringify(body) });
}

function rotateInviteCode(eventId: string): Promise<AccessResponse> {
  return authedJson(`/v1/events/${eventId}/access/code`, { method: "POST", body: "{}" });
}

function setSeats(eventId: string, maxGuests: number | null): Promise<AccessResponse> {
  return authedJson(`/v1/events/${eventId}/access/seats`, {
    method: "POST",
    body: JSON.stringify({ maxGuests }),
  });
}

function setKioskPublic(eventId: string, kioskPublic: boolean): Promise<AccessResponse> {
  return authedJson(`/v1/events/${eventId}/access/kiosk`, {
    method: "POST",
    body: JSON.stringify({ kioskPublic }),
  });
}

function listHostLinks(eventId: string): Promise<{ links: HostLinkRow[] }> {
  return authedJson(`/v1/events/${eventId}/host-links`, { method: "GET" });
}

function revokeHostLink(eventId: string, linkId: string): Promise<unknown> {
  return authedJson(`/v1/events/${eventId}/host-links/${linkId}/revoke`, {
    method: "POST",
    body: "{}",
  });
}

function mintRecoveryCode(eventId: string): Promise<{ recoveryCode: string }> {
  return authedJson(`/v1/events/${eventId}/recovery-code`, { method: "POST", body: "{}" });
}

function isMissingEndpoint(err: unknown): boolean {
  return err instanceof ApiError && (err.status === 404 || err.status === 405 || err.status === 501);
}

/**
 * The door: who can get into this event, how many devices fit, and who else can drive the console.
 *
 * The vocabulary here is load-bearing and is not up for softening. It says **seats**, never "guests",
 * because the cap counts sessions and one person routinely holds several (a phone, a laptop, a rescan
 * after clearing site data) — a host reading "40 of 40 guests" at a 25-person party would conclude the
 * counter is broken, and the real failure mode is somebody's mother locked out at the venue, so
 * raising the number is one tap. And flipping an invite-only event open names its consequence out
 * loud, because that flip widens who may read photographs guests already shared — an exposure change
 * made by someone other than the person who took the photo.
 */
export function AccessPanel({
  event,
  eventId,
  guestCount,
}: {
  event: HostEventDoc;
  eventId: string;
  guestCount?: number | null;
}) {
  const stored = (event as HostEventDoc & { access?: StoredAccess }).access ?? {};
  const [access, setAccess] = useState<AccessResponse>(() => ({
    eventId,
    mode: stored.mode ?? "open",
    maxGuests: stored.maxGuests ?? null,
    guestCount: guestCount ?? 0,
    joinCode: null,
    joinUrl: null,
    codeRotatedAt: stored.codeRotatedAt ?? null,
    kioskPublic: stored.kioskPublic ?? true,
  }));
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [confirmOpen, setConfirmOpen] = useState<string | null>(null);
  const [seatDraft, setSeatDraft] = useState("");
  const [links, setLinks] = useState<HostLinkRow[] | null>(null);
  const [linksListable, setLinksListable] = useState(true);
  const [recoveryCode, setRecoveryCode] = useState<string | null>(null);

  // The event document is the live source of truth (the console holds an onSnapshot on it), so a
  // change made from another device shows up here without this panel asking.
  useEffect(() => {
    setAccess((prev) => ({
      ...prev,
      mode: stored.mode ?? "open",
      maxGuests: stored.maxGuests ?? null,
      codeRotatedAt: stored.codeRotatedAt ?? null,
      kioskPublic: stored.kioskPublic ?? true,
      guestCount: guestCount ?? prev.guestCount,
    }));
  }, [stored.mode, stored.maxGuests, stored.codeRotatedAt, stored.kioskPublic, guestCount]);

  const loadLinks = useCallback(() => {
    listHostLinks(eventId).then(
      (res) => setLinks(res.links),
      (err) => {
        // No list endpoint in this deployment: fall back to showing only what this session minted,
        // rather than an empty list that would imply no co-hosts exist.
        if (isMissingEndpoint(err)) setLinksListable(false);
        setLinks((prev) => prev ?? []);
      }
    );
  }, [eventId]);

  useEffect(() => {
    loadLinks();
  }, [loadLinks]);

  async function run<T>(key: string, fn: () => Promise<T>, onOk: (res: T) => void) {
    setBusy(key);
    setError(null);
    setNotice(null);
    try {
      onOk(await fn());
    } catch (err) {
      setError(
        isMissingEndpoint(err)
          ? "This deployment's API doesn't support that control yet. Nothing changed."
          : err instanceof ApiError
            ? err.message
            : "That didn't go through. Nothing changed — try again."
      );
    } finally {
      setBusy(null);
    }
  }

  function applyAccess(res: AccessResponse) {
    // A plaintext code comes back only from a rotation or a first flip to invite; every other
    // response carries `null` there, and merging rather than replacing is what stops an unrelated
    // save (raising the seat count, say) from blanking a code the host hasn't copied yet.
    setAccess((prev) => ({
      ...res,
      joinCode: res.joinCode ?? (res.mode === "invite" ? prev.joinCode : null),
      joinUrl: res.joinUrl ?? (res.mode === "invite" ? prev.joinUrl : null),
    }));
    if (res.joinCode) setNotice("New invite code below — copy it now; it can't be shown again.");
  }

  const isInvite = access.mode === "invite";
  const seatsUsed = access.guestCount;
  const cap = access.maxGuests ?? null;
  const nearlyFull = cap !== null && seatsUsed >= cap * 0.85;

  return (
    <section className="mb-10 glass-card p-6 rounded-3xl border border-white/10 shadow-xl">
      <div className="flex items-center gap-2 mb-2">
        {isInvite ? (
          <Lock className="w-4 h-4 text-[var(--accent)]" />
        ) : (
          <DoorOpen className="w-4 h-4 text-[var(--accent)]" />
        )}
        <h3 className="font-[family-name:var(--font-display)] text-lg font-medium text-[var(--ivory)]">
          Who can get in
        </h3>
      </div>
      <p className="text-xs text-[var(--ink-muted)] mb-5 leading-relaxed">
        Control the door, the seat count, and who else can drive this console. Changes take effect
        immediately.
      </p>

      {/* ------------------------------------------------------------- mode */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5 mb-5">
        <ModeCard
          active={!isInvite}
          icon={DoorOpen}
          title="Open"
          body="Anyone who scans the QR code or opens the link joins. Best for a venue with the code on the wall."
          busy={busy === "mode-open"}
          onClick={() => {
            if (isInvite) {
              setConfirmOpen("open");
              return;
            }
            void run("mode-open", () => setAccessMode(eventId, { mode: "open" }), applyAccess);
          }}
        />
        <ModeCard
          active={isInvite}
          icon={Lock}
          title="Invite only"
          body="A code is required to join, and photo files stop being readable without one. Best for a private party."
          busy={busy === "mode-invite"}
          onClick={() => {
            if (isInvite) return;
            void run("mode-invite", () => setAccessMode(eventId, { mode: "invite" }), applyAccess);
          }}
        />
      </div>

      {/* The honesty requirement: this copy names what the flip actually does, and the server
          refuses the flip without it. */}
      {confirmOpen === "open" && (
        <div className="mb-5 p-5 rounded-2xl bg-[var(--warn)]/10 border border-[var(--warn)]/35">
          <div className="flex items-center gap-2 mb-2 text-xs font-semibold text-[var(--warn)]">
            <AlertTriangle className="w-4 h-4" />
            <span>Open the door to anyone with the link?</span>
          </div>
          <p className="text-xs text-[var(--ivory-dim)] leading-relaxed mb-2">
            Photos your guests have <strong>already shared</strong> become reachable by anyone who
            joins this event&rsquo;s link. Nothing that&rsquo;s private becomes public, and every guest
            keeps the padlock on their own photos — but the door stops asking for a code.
          </p>
          <p className="text-[11px] text-[var(--ink-muted)] leading-relaxed mb-4">
            This is recorded in your event&rsquo;s activity log, and you can shut the door again at any
            time.
          </p>
          <div className="flex flex-col sm:flex-row gap-2">
            <button
              type="button"
              disabled={busy === "mode-open"}
              onClick={() =>
                void run(
                  "mode-open",
                  () => setAccessMode(eventId, { mode: "open", confirm: true }),
                  (res) => {
                    applyAccess(res);
                    setConfirmOpen(null);
                  }
                )
              }
              className="flex-1 py-3 rounded-full text-xs font-semibold bg-[var(--warn)]/20 border border-[var(--warn)]/45 text-[var(--warn)] hover:bg-[var(--warn)]/30 transition-colors disabled:opacity-40"
            >
              {busy === "mode-open" ? "Opening…" : "Yes, open the event"}
            </button>
            <button
              type="button"
              onClick={() => setConfirmOpen(null)}
              className="btn-secondary flex-1 py-3 text-xs font-semibold"
            >
              Keep it invite only
            </button>
          </div>
        </div>
      )}

      {/* ------------------------------------------------------------- invite code */}
      {isInvite && (
        <div className="mb-5 p-5 rounded-2xl bg-white/[0.03] border border-white/10">
          <div className="flex items-center gap-2 mb-2">
            <KeyRound className="w-4 h-4 text-[var(--accent)]" />
            <h4 className="text-xs font-semibold text-[var(--ivory)] uppercase tracking-wider">
              Invite code
            </h4>
          </div>

          {access.joinCode ? (
            <>
              <CopyRow value={access.joinCode} label="Invite code" mono />
              {access.joinUrl && <CopyRow value={access.joinUrl} label="Join link" small />}
              <p className="text-[11px] text-[var(--warn)] mt-2 leading-relaxed">
                Copy this now. Only a fingerprint of it is stored, so it can never be shown again —
                if it&rsquo;s lost, rotate for a new one.
              </p>
            </>
          ) : (
            <p className="text-xs text-[var(--ink-muted)] leading-relaxed">
              Your code is live but hidden — only a fingerprint of it is stored, never the code
              itself, so nobody (including us) can read it back. Rotate to get a fresh one.
            </p>
          )}

          <button
            type="button"
            disabled={busy === "rotate"}
            onClick={() => void run("rotate", () => rotateInviteCode(eventId), applyAccess)}
            className="btn-secondary mt-3 px-4 py-2.5 text-xs font-semibold flex items-center gap-2 disabled:opacity-40"
          >
            <RefreshCw className="w-3.5 h-3.5" />
            <span>{busy === "rotate" ? "Rotating…" : "Rotate the code"}</span>
          </button>
          <p className="text-[11px] text-[var(--ink-faint)] mt-2 leading-relaxed">
            Rotating kills every link built on the old code instantly. Guests already inside stay
            inside — they hold a pass, not a code.
          </p>
        </div>
      )}

      {/* ------------------------------------------------------------- seats */}
      {isInvite && (
        <div className="mb-5 p-5 rounded-2xl bg-white/[0.03] border border-white/10">
          <div className="flex items-center justify-between gap-3 mb-2">
            <div className="flex items-center gap-2">
              <Users className="w-4 h-4 text-[var(--accent)]" />
              <h4 className="text-xs font-semibold text-[var(--ivory)] uppercase tracking-wider">
                Seats
              </h4>
            </div>
            <span
              className={`text-sm font-mono font-bold tabular-nums ${
                nearlyFull ? "text-[var(--warn)]" : "text-[var(--ivory)]"
              }`}
            >
              {seatsUsed}
              {cap === null ? " / unlimited" : ` / ${cap}`}
            </span>
          </div>

          <p className="text-xs text-[var(--ink-muted)] leading-relaxed mb-1">
            Seats count <strong className="text-[var(--ivory-dim)]">devices, not people</strong>. One
            guest often takes two or three — their phone, a tablet, a re-scan after clearing their
            browser — so this number always reads higher than your guest list. Set it generously.
          </p>
          <p className="text-[11px] text-[var(--ink-faint)] leading-relaxed mb-4">
            Lowering it never ejects anyone; the door just stops admitting new devices.
          </p>

          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              disabled={busy === "seats"}
              onClick={() =>
                void run(
                  "seats",
                  () => setSeats(eventId, (cap ?? seatsUsed) + SEAT_STEP),
                  applyAccess
                )
              }
              className="btn-primary px-4 py-2.5 rounded-full text-xs font-semibold flex items-center gap-1.5 disabled:opacity-40"
            >
              <Plus className="w-3.5 h-3.5 stroke-[3]" />
              <span>{busy === "seats" ? "Saving…" : `Add ${SEAT_STEP} seats`}</span>
            </button>

            <div className="flex items-center gap-2">
              <input
                type="number"
                min={1}
                inputMode="numeric"
                value={seatDraft}
                onChange={(e) => setSeatDraft(e.target.value)}
                placeholder={cap === null ? "no cap" : String(cap)}
                aria-label="Set an exact seat count"
                className="w-24 px-3 py-2.5 rounded-xl bg-black/50 border border-white/10 text-xs font-mono tabular-nums text-[var(--ivory)] placeholder:text-[var(--ink-faint)] focus:border-[var(--accent)] focus:outline-none"
              />
              <button
                type="button"
                disabled={busy === "seats-exact" || !seatDraft.trim()}
                onClick={() => {
                  const n = Number.parseInt(seatDraft, 10);
                  if (!Number.isFinite(n) || n < 1) {
                    setError("A seat count has to be a whole number, 1 or more.");
                    return;
                  }
                  void run("seats-exact", () => setSeats(eventId, n), (res) => {
                    applyAccess(res);
                    setSeatDraft("");
                  });
                }}
                className="btn-secondary px-4 py-2.5 text-xs font-semibold disabled:opacity-40"
              >
                {busy === "seats-exact" ? "Saving…" : "Set"}
              </button>
            </div>

            {cap !== null && (
              <button
                type="button"
                disabled={busy === "seats-none"}
                onClick={() => void run("seats-none", () => setSeats(eventId, null), applyAccess)}
                className="text-xs font-semibold text-[var(--ink-muted)] hover:text-[var(--accent)] transition-colors underline decoration-dotted underline-offset-4 disabled:opacity-40"
              >
                Remove the cap
              </button>
            )}
          </div>
        </div>
      )}

      {/* ------------------------------------------------------------- kiosk */}
      <div className="mb-5 p-5 rounded-2xl bg-white/[0.03] border border-white/10">
        <div className="flex items-center gap-2 mb-2">
          {access.kioskPublic ? (
            <Tv className="w-4 h-4 text-[var(--accent)]" />
          ) : (
            <EyeOff className="w-4 h-4 text-[var(--accent)]" />
          )}
          <h4 className="text-xs font-semibold text-[var(--ivory)] uppercase tracking-wider">
            The big screen
          </h4>
        </div>
        <p className="text-xs text-[var(--ink-muted)] leading-relaxed mb-4">
          {access.kioskPublic
            ? "Anyone with the kiosk link can put this event's show on a screen — handy when the venue's AV person sets it up for you."
            : "Only you and your co-hosts can open the show. The venue will need one of your links to put it on a screen."}
        </p>
        <button
          type="button"
          disabled={busy === "kiosk"}
          onClick={() =>
            void run("kiosk", () => setKioskPublic(eventId, !access.kioskPublic), applyAccess)
          }
          className="btn-secondary px-4 py-2.5 text-xs font-semibold disabled:opacity-40"
        >
          {busy === "kiosk"
            ? "Saving…"
            : access.kioskPublic
              ? "Require a link to open the show"
              : "Let anyone with the link open the show"}
        </button>
      </div>

      {/* ------------------------------------------------------------- co-hosts */}
      <div className="mb-5 p-5 rounded-2xl bg-white/[0.03] border border-white/10">
        <div className="flex items-center gap-2 mb-2">
          <UserPlus className="w-4 h-4 text-[var(--accent)]" />
          <h4 className="text-xs font-semibold text-[var(--ivory)] uppercase tracking-wider">
            Co-hosts
          </h4>
        </div>
        <p className="text-xs text-[var(--ink-muted)] leading-relaxed mb-4">
          A co-host link gives whoever opens it this same console — the review queues, the stage
          controls, the freeze switch. Send it to the one person actually running the room with you,
          not to the group chat.
        </p>

        {links !== null && links.length > 0 && (
          <ul className="space-y-2 mb-3">
            {links
              .filter((l) => l.active !== false && !l.revokedAt && !l.revoked)
              .map((l, i) => (
                <li
                  key={l.linkId ?? l.url ?? i}
                  className="flex items-center gap-2 p-2.5 rounded-xl bg-white/5 border border-white/10"
                >
                  <span className="flex-1 min-w-0 text-[11px] font-mono text-[var(--ink-muted)] truncate">
                    {l.url ?? linkLabel(l)}
                  </span>
                  {l.url && <CopyButton value={l.url} />}
                  {l.linkId && (
                    <button
                      type="button"
                      aria-label="Revoke this co-host link"
                      disabled={busy === `revoke-${l.linkId}`}
                      onClick={() =>
                        void run(
                          `revoke-${l.linkId}`,
                          () => revokeHostLink(eventId, l.linkId as string),
                          () => {
                            setNotice("Revoked — that link no longer opens the console.");
                            loadLinks();
                          }
                        )
                      }
                      className="shrink-0 w-8 h-8 rounded-full text-[var(--danger)] hover:bg-[var(--danger)]/15 flex items-center justify-center transition-colors disabled:opacity-40"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  )}
                </li>
              ))}
          </ul>
        )}

        <button
          type="button"
          disabled={busy === "mint-link"}
          onClick={() =>
            void run(
              "mint-link",
              () => createHostLink(eventId),
              (res) => {
                setLinks((prev) => [
                  { url: res.url, expiresAt: res.expiresAt, createdAt: new Date().toISOString() },
                  ...(prev ?? []),
                ]);
                setNotice("Co-host link ready — copy it from the list above.");
              }
            )
          }
          className="btn-secondary px-4 py-2.5 text-xs font-semibold flex items-center gap-2 disabled:opacity-40"
        >
          <UserPlus className="w-3.5 h-3.5" />
          <span>{busy === "mint-link" ? "Minting…" : "Create a co-host link"}</span>
        </button>

        {!linksListable && (
          <p className="text-[11px] text-[var(--ink-faint)] mt-2 leading-relaxed">
            Only links created in this browser session are listed — this deployment doesn&rsquo;t serve
            the full list yet. Existing co-hosts keep their access regardless.
          </p>
        )}
      </div>

      {/* ------------------------------------------------------------- recovery code */}
      <div className="p-5 rounded-2xl bg-white/[0.03] border border-white/10">
        <div className="flex items-center gap-2 mb-2">
          <KeyRound className="w-4 h-4 text-[var(--accent)]" />
          <h4 className="text-xs font-semibold text-[var(--ivory)] uppercase tracking-wider">
            Your recovery code
          </h4>
        </div>
        {recoveryCode ? (
          <>
            <CopyRow value={recoveryCode} label="Recovery code" mono />
            <p className="text-[11px] text-[var(--warn)] mt-2 leading-relaxed">
              Save this somewhere you&rsquo;ll still have it tomorrow. The old code has stopped working.
            </p>
          </>
        ) : (
          <p className="text-xs text-[var(--ink-muted)] leading-relaxed">
            We can&rsquo;t show you the code you were given at setup — only a fingerprint of it is
            stored, which is the point. If you&rsquo;ve lost it, make a new one; the old one stops
            working the moment you do.
          </p>
        )}
        <button
          type="button"
          disabled={busy === "recovery"}
          onClick={() =>
            void run("recovery", () => mintRecoveryCode(eventId), (res) =>
              setRecoveryCode(res.recoveryCode)
            )
          }
          className="btn-secondary mt-3 px-4 py-2.5 text-xs font-semibold flex items-center gap-2 disabled:opacity-40"
        >
          <RefreshCw className="w-3.5 h-3.5" />
          <span>{busy === "recovery" ? "Minting…" : "Make a new recovery code"}</span>
        </button>
      </div>

      {notice && <p className="text-xs text-[var(--ok)] mt-4 leading-relaxed">{notice}</p>}
      {error && <p className="text-xs text-[var(--danger)] mt-4 leading-relaxed">{error}</p>}
    </section>
  );
}

function ModeCard({
  active,
  icon: Icon,
  title,
  body,
  busy,
  onClick,
}: {
  active: boolean;
  icon: React.ElementType;
  title: string;
  body: string;
  busy: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={busy}
      aria-pressed={active}
      className={`text-left p-4 rounded-2xl transition-all border flex flex-col gap-2 disabled:opacity-60 ${
        active
          ? "bg-[var(--bg-2)] border-2 border-[var(--accent)] shadow-lg"
          : "bg-[var(--bg-1)]/60 border-white/5 hover:border-white/20"
      }`}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="flex items-center gap-2 font-semibold text-sm text-[var(--ivory)]">
          <Icon className="w-4 h-4 text-[var(--accent)]" />
          <span>{title}</span>
        </span>
        {active && <Check className="w-4 h-4 stroke-[3] text-[var(--accent)]" />}
      </div>
      <p className="text-[11px] text-[var(--ink-muted)] leading-relaxed">{body}</p>
    </button>
  );
}

function CopyButton({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      aria-label="Copy"
      onClick={() => {
        void navigator.clipboard?.writeText(value);
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
      }}
      className="shrink-0 flex items-center gap-1 text-[11px] px-2.5 py-1.5 rounded-md bg-white/5 hover:bg-white/15 text-[var(--ivory)] transition-colors"
    >
      {copied ? <Check className="w-3 h-3 text-[var(--ok)]" /> : <Copy className="w-3 h-3" />}
      <span>{copied ? "Copied" : "Copy"}</span>
    </button>
  );
}

function CopyRow({
  value,
  label,
  mono,
  small,
}: {
  value: string;
  label: string;
  mono?: boolean;
  small?: boolean;
}) {
  return (
    <div className="mt-2">
      <div className="flex items-center justify-between gap-2 mb-1">
        <span className="text-[11px] font-semibold text-[var(--ink-muted)]">{label}</span>
        <CopyButton value={value} />
      </div>
      <p
        className={`${mono ? "font-mono" : ""} ${
          small ? "text-[11px]" : "text-sm"
        } break-all text-[var(--ivory)] bg-black/40 p-3 rounded-xl border border-white/5`}
      >
        {value}
      </p>
    </div>
  );
}
