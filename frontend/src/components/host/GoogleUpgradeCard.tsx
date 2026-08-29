"use client";

import { useState } from "react";
import { GoogleAuthProvider, linkWithPopup } from "firebase/auth";
import { Check, ShieldCheck, UserPlus } from "lucide-react";
import { auth } from "@/lib/firebase";

/** Optional Google attachment, offered *after* the event exists — never before.
 *
 * Event creation is anonymous and stays that way: a sign-in wall in front of "make me an event" is
 * where someone evaluating this abandons it, and there is nothing to authenticate against yet
 * anyway. What this fixes is the tail risk on the other side — the recovery code is shown once, and
 * a host who loses it and clears their browser has lost the event.
 *
 * `linkWithPopup` is the whole mechanism: it attaches a Google credential to the *existing*
 * anonymous uid, so the `host` custom claim minted at creation stays exactly where it is. No
 * migration, no re-grant, nothing server-side to change. Declining costs the host nothing — the
 * recovery code keeps working either way, which is why this is a card and not a step.
 *
 * Written inline here rather than as a `lib/firebase.ts` helper because that file is another
 * workstream's surface this session; if it later grows a `linkGoogleAccount()`, this should call it.
 */
export function GoogleUpgradeCard() {
  const [state, setState] = useState<"idle" | "busy" | "linked">("idle");
  const [email, setEmail] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function link() {
    const user = auth?.currentUser;
    if (!user) {
      setError("Your session expired. Reload the page and try again — your event is safe.");
      return;
    }
    setState("busy");
    setError(null);
    try {
      const provider = new GoogleAuthProvider();
      const cred = await linkWithPopup(user, provider);
      setEmail(cred.user.email ?? null);
      setState("linked");
    } catch (err) {
      const code = (err as { code?: string })?.code ?? "";
      if (code === "auth/credential-already-in-use" || code === "auth/email-already-in-use") {
        setError(
          "That Google account is already attached to a different Showrunner session. Keep your recovery code — it's all you need — or try another account."
        );
      } else if (code === "auth/popup-blocked") {
        setError("Your browser blocked the popup. Allow popups for this site and try again.");
      } else if (
        code === "auth/popup-closed-by-user" ||
        code === "auth/cancelled-popup-request" ||
        code === "auth/user-cancelled"
      ) {
        setError(null);
      } else {
        setError("Couldn't attach that account. Your event and your recovery code are unaffected.");
      }
      setState("idle");
    }
  }

  if (state === "linked") {
    return (
      <div className="rounded-2xl p-5 border border-[var(--ok)]/30 bg-[var(--ok)]/10">
        <div className="flex items-center gap-2 text-xs font-semibold text-[var(--ok)] mb-1.5">
          <Check className="w-4 h-4 stroke-[3]" />
          <span>Google account attached</span>
        </div>
        <p className="text-xs text-[var(--ivory-dim)] leading-relaxed">
          {email ? <span className="font-mono">{email}</span> : "This account"} can now open this
          event&rsquo;s console from any device. Hang on to the recovery code as well — it still works
          and it needs no account.
        </p>
      </div>
    );
  }

  return (
    <div className="rounded-2xl glass-card p-5 border border-white/10">
      <div className="flex items-center gap-2 mb-1.5">
        <UserPlus className="w-4 h-4 text-[var(--accent)]" />
        <span className="text-xs font-semibold text-[var(--ivory)]">
          Optional: get back in without the code
        </span>
      </div>
      <p className="text-xs text-[var(--ink-muted)] leading-relaxed mb-4">
        Attach a Google account and you can reopen this console from any device, without needing the
        code above. One tap, nothing changes about the event, and skipping it costs you nothing.
      </p>

      <button
        type="button"
        onClick={() => void link()}
        disabled={state === "busy"}
        className="btn-secondary w-full py-3 rounded-full text-xs font-semibold flex items-center justify-center gap-2 disabled:opacity-40"
      >
        <ShieldCheck className="w-4 h-4" />
        <span>{state === "busy" ? "Waiting for Google…" : "Attach a Google account"}</span>
      </button>

      {error && <p className="text-xs text-[var(--warn)] mt-3 leading-relaxed">{error}</p>}
    </div>
  );
}
