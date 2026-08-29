"use client";

import { useEffect, useState } from "react";
import { ArrowRight, KeyRound, X, CalendarCheck } from "lucide-react";
import { ensureAnonymousAuth } from "@/lib/firebase";
import { redeemHostCode } from "@/lib/hostApi";
import { ApiError } from "@/lib/api";
import { forgetEvent, listRememberedEvents, rememberEvent, type RememberedEvent } from "./rememberedEvents";

/** The "I already have an event" path, on `/host` where it belongs.
 *
 * The recovery-code box used to exist only inside `/host/{eventId}`, which is a closed loop: the code
 * is what identifies the event, but you had to already know the event's id to reach the box you'd
 * type it into. `POST /v1/host-claim` takes a bare code and answers with the event, so the box works
 * perfectly well before the id is known — it just had to be put somewhere reachable.
 *
 * Sits above the creation wizard rather than beside it: creating is the primary action for anyone
 * arriving here, so this stays quiet unless the browser actually remembers an event.
 */
export function HostReturnPanel() {
  const [remembered, setRemembered] = useState<RememberedEvent[]>([]);
  const [codeOpen, setCodeOpen] = useState(false);
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setRemembered(listRememberedEvents());
  }, []);

  async function submit() {
    const value = code.trim();
    if (!value) return;
    setBusy(true);
    setError(null);
    try {
      await ensureAnonymousAuth();
      const res = await redeemHostCode(value);
      rememberEvent(res.eventId, res.eventName ?? res.eventId);
      window.location.assign(`/host/${encodeURIComponent(res.eventId)}`);
    } catch (err) {
      setError(
        err instanceof ApiError && (err.status === 404 || err.status === 403)
          ? "That code doesn't match an event. Codes are single-purpose — check you're using the host recovery code, not a guest invite code."
          : "That code didn't work. Check it and try again."
      );
      setBusy(false);
    }
  }

  const hasRemembered = remembered.length > 0;
  if (!hasRemembered && !codeOpen) {
    return (
      <div className="max-w-2xl mx-auto px-5 pt-10 -mb-6 text-center">
        <button
          type="button"
          onClick={() => setCodeOpen(true)}
          className="text-xs font-semibold text-[var(--ink-muted)] hover:text-[var(--accent)] transition-colors underline decoration-dotted underline-offset-4"
        >
          I already have an event
        </button>
      </div>
    );
  }

  return (
    <div className="max-w-2xl mx-auto px-5 pt-10">
      <section className="glass-card p-6 rounded-3xl border border-white/10 shadow-lg">
        <div className="flex items-center gap-2 mb-1">
          <CalendarCheck className="w-4 h-4 text-[var(--accent)]" />
          <h2 className="text-xs font-semibold text-[var(--ivory)] uppercase tracking-wider">
            {hasRemembered ? "Your events" : "Already have an event?"}
          </h2>
        </div>

        {hasRemembered && (
          <>
            <p className="text-xs text-[var(--ink-muted)] mb-4 leading-relaxed">
              Created from this browser. Only this device remembers them — your recovery code is what
              gets you back in anywhere else.
            </p>
            <ul className="space-y-2 mb-4">
              {remembered.map((e) => (
                <li
                  key={e.eventId}
                  className="flex items-center gap-2 p-3 rounded-xl bg-white/5 border border-white/10"
                >
                  <a
                    href={`/host/${encodeURIComponent(e.eventId)}`}
                    className="flex-1 min-w-0 group"
                  >
                    <span className="block text-sm font-medium text-[var(--ivory)] group-hover:text-[var(--accent)] truncate transition-colors">
                      {e.name}
                    </span>
                    <span className="block text-[11px] font-mono text-[var(--ink-faint)] truncate">
                      {e.eventId}
                    </span>
                  </a>
                  <a
                    href={`/host/${encodeURIComponent(e.eventId)}`}
                    className="btn-secondary shrink-0 px-3.5 py-2 text-[11px] font-semibold flex items-center gap-1.5"
                  >
                    <span>Continue</span>
                    <ArrowRight className="w-3.5 h-3.5 stroke-[2.5]" />
                  </a>
                  <button
                    type="button"
                    aria-label={`Forget ${e.name} on this device`}
                    title="Forget on this device"
                    onClick={() => {
                      forgetEvent(e.eventId);
                      setRemembered(listRememberedEvents());
                    }}
                    className="shrink-0 w-8 h-8 rounded-full text-[var(--ink-faint)] hover:text-[var(--ivory)] hover:bg-white/10 flex items-center justify-center transition-colors"
                  >
                    <X className="w-3.5 h-3.5" />
                  </button>
                </li>
              ))}
            </ul>
          </>
        )}

        {codeOpen ? (
          <div className="pt-1">
            <label
              htmlFor="host-recovery-code"
              className="block text-[11px] font-semibold text-[var(--ivory)] uppercase tracking-wider mb-2"
            >
              Host recovery code
            </label>
            <p className="text-xs text-[var(--ink-muted)] mb-3 leading-relaxed">
              The code you saved when you created the event. It tells us which event you mean — you
              don&rsquo;t need the event&rsquo;s id.
            </p>
            <div className="flex flex-col sm:flex-row gap-2">
              <input
                id="host-recovery-code"
                value={code}
                onChange={(e) => {
                  setCode(e.target.value);
                  setError(null);
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter") void submit();
                }}
                autoComplete="off"
                spellCheck={false}
                placeholder="Paste your recovery code"
                className="flex-1 px-4 py-3 rounded-xl bg-black/50 border border-white/10 text-sm font-mono text-[var(--ivory)] placeholder:text-[var(--ink-faint)] focus:border-[var(--accent)] focus:outline-none transition-colors"
              />
              <button
                type="button"
                disabled={busy || !code.trim()}
                onClick={() => void submit()}
                className="btn-primary px-5 py-3 rounded-full text-sm font-semibold flex items-center justify-center gap-2 disabled:opacity-40 shrink-0"
              >
                <KeyRound className="w-4 h-4" />
                <span>{busy ? "Checking…" : "Open my event"}</span>
              </button>
            </div>
            {error && <p className="text-xs text-[var(--danger)] mt-3 leading-relaxed">{error}</p>}
          </div>
        ) : (
          <button
            type="button"
            onClick={() => setCodeOpen(true)}
            className="text-xs font-semibold text-[var(--accent)] hover:underline"
          >
            Use a recovery code instead
          </button>
        )}
      </section>
    </div>
  );
}
