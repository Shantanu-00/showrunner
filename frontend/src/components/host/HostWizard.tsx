"use client";

import { useState } from "react";
import { Sparkles, Calendar, Clock, Globe, ArrowRight, Copy, Check, ShieldCheck, KeyRound } from "lucide-react";
import { ensureAnonymousAuth } from "@/lib/firebase";
import { createEvent } from "@/lib/hostApi";
import { ApiError } from "@/lib/api";
import { TEMPLATE_LABELS, type EventTemplateId } from "@/lib/hostTypes";
import { GoogleUpgradeCard } from "./GoogleUpgradeCard";
import { HostReturnPanel } from "./HostReturnPanel";
import { rememberEvent } from "./rememberedEvents";

const TEMPLATES: Array<{ id: EventTemplateId; name: string; desc: string; colors: [string, string] }> = [
  { id: "wedding_hindu", name: "Hindu Wedding", desc: "Baraat gold & crimson palette", colors: ["#d4af6a", "#8c1d2f"] },
  { id: "wedding_generic", name: "Classic Wedding", desc: "Champagne ballroom & rose", colors: ["#e9cf9a", "#b76e79"] },
  { id: "wedding_christian", name: "Christian Wedding", desc: "Ivory elegance & gold", colors: ["#e9cf9a", "#b76e79"] },
  { id: "wedding_muslim", name: "Muslim Wedding", desc: "Emerald & champagne gold", colors: ["#e9cf9a", "#b76e79"] },
  { id: "birthday", name: "Birthday Party", desc: "Vibrant coral & candlelight gold", colors: ["#f27059", "#f2c14e"] },
  { id: "bachelor_bachelorette", name: "After Hours / Party", desc: "Electric violet & acid mint", colors: ["#a855f7", "#06b6d4"] },
  { id: "graduation", name: "Graduation Gala", desc: "Cord gold & midnight navy", colors: ["#eab308", "#38bdf8"] },
  { id: "corporate_offsite", name: "Keynote / Offsite", desc: "Ice blue & precision silver", colors: ["#38bdf8", "#94a3b8"] },
  { id: "custom", name: "Custom Event", desc: "Neutral baseline with adaptive dials", colors: ["#e9cf9a", "#b76e79"] },
];

export function HostWizard() {
  const [name, setName] = useState("");
  const [timezone, setTimezone] = useState(
    typeof Intl !== "undefined" ? Intl.DateTimeFormat().resolvedOptions().timeZone : "UTC"
  );
  const [templateId, setTemplateId] = useState<EventTemplateId>("wedding_hindu");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copiedCode, setCopiedCode] = useState(false);
  const [copiedLink, setCopiedLink] = useState(false);
  const [created, setCreated] = useState<{ eventId: string; hostLink: string; recoveryCode: string } | null>(
    null
  );

  function pickTemplate(t: EventTemplateId) {
    setTemplateId(t);
    document.documentElement.dataset.theme = t;
  }

  async function submit() {
    if (!name.trim() || !timezone.trim()) {
      setError("Event name and timezone are required.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await ensureAnonymousAuth();
      const res = await createEvent({ name: name.trim(), timezone: timezone.trim(), templateId });
      // Written before the success screen renders: the event id is only ever handed to the client
      // once, and a host who closes this tab without copying the recovery code has otherwise lost
      // the way back. See `rememberedEvents.ts` — convenience, not authority.
      rememberEvent(res.eventId, name.trim());
      setCreated(res);
    } catch (err) {
      setError(err instanceof ApiError ? `Couldn't create event (${err.status}): ${err.message}` : "Something went wrong.");
    } finally {
      setBusy(false);
    }
  }

  if (created) {
    return (
      <div className="max-w-xl mx-auto px-5 py-16 animate-fadeIn">
        <div className="text-center mb-8">
          <div className="w-16 h-16 rounded-full bg-[var(--gold-500)]/15 text-[var(--accent)] flex items-center justify-center mx-auto mb-4 border border-[var(--gold-500)]/30">
            <ShieldCheck className="w-8 h-8 stroke-[2]" />
          </div>
          <h1 className="font-[family-name:var(--font-display)] text-3xl font-semibold text-gold-gradient mb-2">
            Your Event is Live Ready
          </h1>
          <p className="text-xs text-[var(--ink-muted)] max-w-md mx-auto">
            You are authenticated as the primary host. Save your recovery code in a secure location.
          </p>
        </div>

        <div className="space-y-4 mb-8">
          <div className="rounded-2xl glass-card p-5 border border-white/10 shadow-lg">
            <div className="flex items-center justify-between mb-2">
              <div className="flex items-center gap-2 text-xs font-semibold text-[var(--gold-300)]">
                <KeyRound className="w-4 h-4" />
                <span>Host Recovery Code</span>
              </div>
              <button
                type="button"
                onClick={() => {
                  navigator.clipboard.writeText(created.recoveryCode);
                  setCopiedCode(true);
                  setTimeout(() => setCopiedCode(false), 2000);
                }}
                className="flex items-center gap-1 text-[11px] px-2.5 py-1 rounded-md bg-white/5 hover:bg-white/15 text-white"
              >
                {copiedCode ? <Check className="w-3 h-3 text-[var(--ok)]" /> : <Copy className="w-3 h-3" />}
                <span>{copiedCode ? "Copied" : "Copy"}</span>
              </button>
            </div>
            <p className="font-mono text-base break-all text-[var(--ivory)] bg-black/40 p-3 rounded-xl border border-white/5">
              {created.recoveryCode}
            </p>
          </div>

          <div className="rounded-2xl glass-card p-5 border border-white/10 shadow-lg">
            <div className="flex items-center justify-between mb-2">
              <div className="flex items-center gap-2 text-xs font-semibold text-[var(--ivory)]">
                <Globe className="w-4 h-4 text-[var(--accent)]" />
                <span>Co-Host Access Link</span>
              </div>
              <button
                type="button"
                onClick={() => {
                  navigator.clipboard.writeText(created.hostLink);
                  setCopiedLink(true);
                  setTimeout(() => setCopiedLink(false), 2000);
                }}
                className="flex items-center gap-1 text-[11px] px-2.5 py-1 rounded-md bg-white/5 hover:bg-white/15 text-white"
              >
                {copiedLink ? <Check className="w-3 h-3 text-[var(--ok)]" /> : <Copy className="w-3 h-3" />}
                <span>{copiedLink ? "Copied" : "Copy"}</span>
              </button>
            </div>
            <p className="font-mono text-xs break-all text-[var(--ink-muted)] bg-black/40 p-3 rounded-xl border border-white/5 truncate">
              {created.hostLink}
            </p>
          </div>

          <GoogleUpgradeCard />
        </div>

        <a
          href={`/host/${created.eventId}`}
          className="btn-primary w-full py-4 rounded-full flex items-center justify-center gap-2 text-sm font-semibold shadow-2xl"
        >
          <span>Open Host Console</span>
          <ArrowRight className="w-4 h-4 stroke-[2.5]" />
        </a>
      </div>
    );
  }

  return (
    <>
      {/* The way back in, for a host who already has an event — see HostReturnPanel's own note on
          why it could not stay inside /host/{eventId}. */}
      <HostReturnPanel />

      <div className="max-w-2xl mx-auto px-5 py-12">
      <div className="text-center mb-10">
        <div className="flex items-center justify-center gap-2 mb-2">
          <span className="p-1.5 rounded-lg bg-[var(--gold-500)]/15 text-[var(--accent)] border border-[var(--gold-500)]/20">
            <Sparkles className="w-4 h-4" />
          </span>
          <span className="font-mono text-xs uppercase tracking-[0.2em] font-semibold text-[var(--accent)]">
            NEW EVENT SETUP
          </span>
        </div>
        <h1 className="font-[family-name:var(--font-display)] text-3xl font-semibold text-gold-gradient mb-2">
          Create Showrunner Event
        </h1>
        <p className="text-xs text-[var(--ink-muted)] max-w-md mx-auto">
          Configure cultural profile, timeline stages, and launch the autonomous media director.
        </p>
      </div>

      <div className="space-y-6">
        <div className="glass-card p-6 rounded-3xl border border-white/10 space-y-4">
          <div>
            <label className="block text-xs font-semibold text-[var(--ivory)] uppercase tracking-wider mb-2">
              Event Name
            </label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Maya & Rohan's Wedding"
              className="w-full px-4 py-3 rounded-xl bg-black/40 border border-white/10 text-sm text-[var(--ivory)] placeholder:text-[var(--ink-faint)] focus:border-[var(--accent)] focus:outline-none transition-colors"
            />
          </div>

          <div>
            <label className="block text-xs font-semibold text-[var(--ivory)] uppercase tracking-wider mb-2">
              Timezone (for EXIF temporal alignment)
            </label>
            <input
              type="text"
              value={timezone}
              onChange={(e) => setTimezone(e.target.value)}
              className="w-full px-4 py-3 rounded-xl bg-black/40 border border-white/10 text-sm font-mono text-[var(--ivory)] focus:border-[var(--accent)] focus:outline-none transition-colors"
            />
          </div>
        </div>

        <div>
          <label className="block text-xs font-semibold text-[var(--ivory)] uppercase tracking-wider mb-3">
            Choose Event Template & Theme
          </label>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
            {TEMPLATES.map((t) => {
              const isSelected = templateId === t.id;
              return (
                <button
                  key={t.id}
                  type="button"
                  onClick={() => pickTemplate(t.id)}
                  className={`text-left p-4 rounded-2xl transition-all border flex flex-col justify-between gap-2 ${
                    isSelected
                      ? "bg-[var(--bg-2)] border-2 border-[var(--accent)] shadow-lg"
                      : "bg-[var(--bg-1)]/60 border-white/5 hover:border-white/20"
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <span className="font-semibold text-sm text-[var(--ivory)]">{t.name}</span>
                    <div className="flex items-center gap-1.5">
                      <span className="w-3.5 h-3.5 rounded-full shadow-sm" style={{ background: t.colors[0] }} />
                      <span className="w-3.5 h-3.5 rounded-full shadow-sm" style={{ background: t.colors[1] }} />
                    </div>
                  </div>
                  <p className="text-[11px] text-[var(--ink-muted)] leading-relaxed">{t.desc}</p>
                </button>
              );
            })}
          </div>
        </div>

        {error && (
          <p className="text-xs text-[var(--danger)] text-center p-3 rounded-xl bg-[var(--danger)]/10 border border-[var(--danger)]/20">
            {error}
          </p>
        )}

        <button
          type="button"
          disabled={busy}
          onClick={() => void submit()}
          className="btn-primary w-full py-4 rounded-full text-sm font-semibold flex items-center justify-center gap-2 shadow-2xl disabled:opacity-50"
        >
          {busy ? (
            <span>Minting Event Graph…</span>
          ) : (
            <>
              <span>Create Event & Enter Console</span>
              <ArrowRight className="w-4 h-4 stroke-[2.5]" />
            </>
          )}
        </button>
      </div>
      </div>
    </>
  );
}
