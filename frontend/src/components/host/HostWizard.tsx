"use client";

import { useState } from "react";
import { ensureAnonymousAuth } from "@/lib/firebase";
import { createEvent } from "@/lib/hostApi";
import { ApiError } from "@/lib/api";
import { TEMPLATE_LABELS, type EventTemplateId } from "@/lib/hostTypes";

const TEMPLATES: EventTemplateId[] = [
  "wedding_generic",
  "wedding_hindu",
  "wedding_christian",
  "wedding_muslim",
  "bachelor_bachelorette",
  "birthday",
  "graduation",
  "corporate_offsite",
  "custom",
];

/** `/host` — the event creation wizard (spec 08 §3, spec 12 §5.4). Selecting a template flips
 * `data-theme` on the whole document live — no reload, no re-render (spec 12 §3's "any event,
 * not a wedding app" demo beat) — before the host has even created anything. */
export function HostWizard() {
  const [name, setName] = useState("");
  const [timezone, setTimezone] = useState(
    typeof Intl !== "undefined" ? Intl.DateTimeFormat().resolvedOptions().timeZone : "UTC"
  );
  const [templateId, setTemplateId] = useState<EventTemplateId>("wedding_generic");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<{ eventId: string; hostLink: string; recoveryCode: string } | null>(
    null
  );

  function pickTemplate(t: EventTemplateId) {
    setTemplateId(t);
    document.documentElement.dataset.theme = t;
  }

  async function submit() {
    if (!name.trim() || !timezone.trim()) {
      setError("Name and timezone are required.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await ensureAnonymousAuth();
      const res = await createEvent({ name: name.trim(), timezone: timezone.trim(), templateId });
      setCreated(res);
    } catch (err) {
      setError(err instanceof ApiError ? `Couldn't create the event (${err.status}).` : "Something went wrong.");
    } finally {
      setBusy(false);
    }
  }

  if (created) {
    return (
      <div className="max-w-xl mx-auto px-5 py-16">
        <p className="font-[family-name:var(--font-display)] text-2xl mb-4" style={{ color: "var(--ivory)" }}>
          Your event is set up
        </p>
        <p className="text-sm mb-6" style={{ color: "var(--ink-muted)" }}>
          You&rsquo;re already the host on this device. Save the recovery code somewhere safe — it&rsquo;s
          the only way back in if you lose every host device.
        </p>
        <div
          className="rounded-[var(--radius-card)] p-4 mb-4"
          style={{ border: "var(--hairline)", background: "var(--bg-1)" }}
        >
          <p className="text-xs mb-1" style={{ color: "var(--ink-muted)" }}>
            Recovery code
          </p>
          <p className="font-mono text-lg break-all" style={{ color: "var(--gold-300)" }}>
            {created.recoveryCode}
          </p>
        </div>
        <div
          className="rounded-[var(--radius-card)] p-4 mb-8"
          style={{ border: "var(--hairline)", background: "var(--bg-1)" }}
        >
          <p className="text-xs mb-1" style={{ color: "var(--ink-muted)" }}>
            Co-host invite link
          </p>
          <p className="font-mono text-sm break-all" style={{ color: "var(--ink-muted)" }}>
            {created.hostLink}
          </p>
        </div>
        <a
          href={`/host/${created.eventId}`}
          className="block w-full text-center py-3 rounded-[var(--radius-pill)] font-medium"
          style={{ background: "var(--accent)", color: "var(--bg-0)" }}
        >
          Go to the console →
        </a>
      </div>
    );
  }

  return (
    <div className="max-w-xl mx-auto px-5 py-16">
      <p className="font-[family-name:var(--font-display)] text-2xl mb-1" style={{ color: "var(--ivory)" }}>
        Create your event
      </p>
      <p className="text-sm mb-8" style={{ color: "var(--ink-muted)" }}>
        Not an assistant. A showrunner.
      </p>

      <label className="block text-sm mb-1" style={{ color: "var(--ink-muted)" }}>
        Event name
      </label>
      <input
        value={name}
        onChange={(e) => setName(e.target.value)}
        placeholder="Ananya &amp; Rohan's Wedding"
        className="w-full mb-4 px-4 py-3 rounded-[var(--radius-card)]"
        style={{ background: "var(--bg-1)", border: "var(--hairline)", color: "var(--ivory)" }}
      />

      <label className="block text-sm mb-1" style={{ color: "var(--ink-muted)" }}>
        Timezone (IANA name)
      </label>
      <input
        value={timezone}
        onChange={(e) => setTimezone(e.target.value)}
        placeholder="Asia/Kolkata"
        className="w-full mb-6 px-4 py-3 rounded-[var(--radius-card)]"
        style={{ background: "var(--bg-1)", border: "var(--hairline)", color: "var(--ivory)" }}
      />

      <p className="text-sm mb-3" style={{ color: "var(--ink-muted)" }}>
        Event type — this sets sensible defaults for sensitivity dials and required moments; you can
        edit every one of them next.
      </p>
      <div className="grid grid-cols-2 gap-3 mb-8">
        {TEMPLATES.map((t) => (
          <button
            key={t}
            type="button"
            onClick={() => pickTemplate(t)}
            className="text-left px-4 py-3 rounded-[var(--radius-card)]"
            style={{
              border: templateId === t ? "2px solid var(--accent)" : "var(--hairline)",
              background: "var(--bg-1)",
              color: "var(--ivory)",
            }}
          >
            {TEMPLATE_LABELS[t]}
          </button>
        ))}
      </div>

      {error && (
        <p className="text-sm mb-4" style={{ color: "var(--danger)" }}>
          {error}
        </p>
      )}

      <button
        type="button"
        onClick={() => void submit()}
        disabled={busy}
        className="w-full py-3 rounded-[var(--radius-pill)] font-medium"
        style={{ background: "var(--accent)", color: "var(--bg-0)", opacity: busy ? 0.6 : 1 }}
      >
        {busy ? "Creating…" : "Create event"}
      </button>
    </div>
  );
}
