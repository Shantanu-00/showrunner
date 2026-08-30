"use client";

import { useEffect, useState } from "react";
import { ChevronDown, Lock, Plus, Settings2, X } from "lucide-react";
import { updateProfile } from "@/lib/hostApi";
import { ApiError } from "@/lib/api";
import type { EventTemplateId, HostEventDoc, SensitivityProfile } from "@/lib/hostTypes";
import { EVENT_TEMPLATE_PRESETS, TEMPLATE_LABELS, TEMPLATE_PRESET_ORDER } from "@/lib/hostTypes";

type PdaAlcohol = "public_ok" | "context_dependent" | "private_only";
type Attire = "relaxed" | "standard" | "conservative";
type Topology = "pyramid" | "flat";

const PDA_ALCOHOL_OPTIONS: { value: PdaAlcohol; label: string }[] = [
  { value: "public_ok", label: "Public OK" },
  { value: "context_dependent", label: "Depends on context" },
  { value: "private_only", label: "Private only" },
];

const ATTIRE_OPTIONS: { value: Attire; label: string }[] = [
  { value: "relaxed", label: "Relaxed" },
  { value: "standard", label: "Standard" },
  { value: "conservative", label: "Conservative" },
];

/** Quiet, collapsed-by-default cultural/sensitivity settings (spec 11 §2) — the wizard's old
 * template grid survives only here, as an optional starting point, never a step a host must pass
 * through to create an event (spec 13's pivot). Mounted in `HostConsoleShell`. */
export function SettingsPanel({ event, eventId }: { event: HostEventDoc; eventId: string }) {
  const [open, setOpen] = useState(false);
  const [templateId, setTemplateId] = useState<EventTemplateId>(
    event.eventTypeProfile?.templateId ?? "custom"
  );
  const [vipTopology, setVipTopology] = useState<Topology>(event.eventTypeProfile?.vipTopology ?? "pyramid");
  const [sensitivity, setSensitivity] = useState<SensitivityProfile>(
    event.eventTypeProfile?.sensitivityProfile ?? {
      pda: "context_dependent",
      alcohol: "context_dependent",
      attire: "standard",
    }
  );
  const [glossary, setGlossary] = useState<string[]>(event.eventTypeProfile?.culturalGlossary ?? []);
  const [glossaryDraft, setGlossaryDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<number | null>(null);

  useEffect(() => {
    setTemplateId(event.eventTypeProfile?.templateId ?? "custom");
    setVipTopology(event.eventTypeProfile?.vipTopology ?? "pyramid");
    setSensitivity(
      event.eventTypeProfile?.sensitivityProfile ?? {
        pda: "context_dependent",
        alcohol: "context_dependent",
        attire: "standard",
      }
    );
    setGlossary(event.eventTypeProfile?.culturalGlossary ?? []);
  }, [event.eventId]); // eslint-disable-line react-hooks/exhaustive-deps

  const isDraft = event.status === "draft";

  function applyPreset(id: EventTemplateId) {
    setTemplateId(id);
    const preset = EVENT_TEMPLATE_PRESETS[id];
    if (preset) {
      setVipTopology(preset.vipTopology);
      setSensitivity(preset.sensitivityProfile);
      setGlossary(preset.culturalGlossary);
    }
  }

  function addGlossaryTerm() {
    const term = glossaryDraft.trim();
    if (!term || glossary.includes(term)) {
      setGlossaryDraft("");
      return;
    }
    setGlossary((prev) => [...prev, term]);
    setGlossaryDraft("");
  }

  function removeGlossaryTerm(term: string) {
    setGlossary((prev) => prev.filter((t) => t !== term));
  }

  async function handleSave() {
    setSaving(true);
    setError(null);
    try {
      await updateProfile(eventId, {
        templateId,
        vipTopology,
        sensitivityProfile: sensitivity,
        culturalGlossary: glossary,
      });
      setSavedAt(Date.now());
    } catch (err) {
      if (err instanceof ApiError && err.code === "NOT_DRAFT") {
        // The server still guards the whole endpoint on draft-only, even though only the dials
        // (which the console leaves enabled post-go-live) may have changed — a live split is
        // flagged as pending in the task brief. Surface its own sentence rather than a generic
        // failure, so a host who only touched a dial after go-live understands why nothing saved.
        setError(err.message);
      } else {
        setError(err instanceof ApiError ? `Couldn't save (${err.status}): ${err.message}` : "Something went wrong.");
      }
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="mb-10 glass-card rounded-3xl border border-white/10 shadow-xl overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between gap-3 p-6 text-left"
        aria-expanded={open}
      >
        <div className="flex items-center gap-2">
          <Settings2 className="w-4 h-4 text-[var(--accent)]" />
          <div>
            <h3 className="font-[family-name:var(--font-display)] text-lg font-medium text-[var(--ivory)]">
              Event settings
            </h3>
            <p className="text-xs text-[var(--ink-muted)]">Cultural context and sensitivity dials — quiet by design.</p>
          </div>
        </div>
        <ChevronDown className={`w-4 h-4 text-[var(--ink-muted)] transition-transform ${open ? "rotate-180" : ""}`} />
      </button>

      {open && (
        <div className="px-6 pb-6 space-y-6 border-t border-white/10 pt-5">
          {/* --------------------------------------------------------------- preset */}
          <div>
            <div className="flex items-center justify-between mb-2">
              <label className="block text-xs font-semibold text-[var(--ivory)] uppercase tracking-wider">
                Start from a preset…
              </label>
              {!isDraft && <LockedNote />}
            </div>
            <select
              value={templateId}
              disabled={!isDraft}
              onChange={(e) => applyPreset(e.target.value as EventTemplateId)}
              className="w-full sm:w-auto px-4 py-2.5 rounded-xl bg-black/40 border border-white/10 text-sm text-[var(--ivory)] focus:border-[var(--accent)] focus:outline-none disabled:opacity-50"
            >
              {TEMPLATE_PRESET_ORDER.map((id) => (
                <option key={id} value={id}>
                  {TEMPLATE_LABELS[id]}
                </option>
              ))}
            </select>
            <p className="text-[11px] text-[var(--ink-faint)] mt-1.5">
              Prefills the dials, topology and glossary below — every field stays editable, nothing is
              silently authoritative.
            </p>
          </div>

          {/* --------------------------------------------------------------- sensitivity dials */}
          <div className="space-y-4">
            <p className="text-xs font-semibold text-[var(--ivory)] uppercase tracking-wider">
              Sensitivity dials
            </p>
            <p className="text-[11px] text-[var(--ink-faint)] leading-relaxed -mt-2">
              These are ceilings, never floors — a dial can only hold photos back from public
              surfaces, never release something the moment itself calls for keeping private.
            </p>

            <DialRow
              label="PDA"
              value={sensitivity.pda as PdaAlcohol}
              options={PDA_ALCOHOL_OPTIONS}
              onChange={(v) => setSensitivity((s) => ({ ...s, pda: v }))}
            />
            <DialRow
              label="Alcohol"
              value={sensitivity.alcohol as PdaAlcohol}
              options={PDA_ALCOHOL_OPTIONS}
              onChange={(v) => setSensitivity((s) => ({ ...s, alcohol: v }))}
            />
            <DialRow
              label="Attire"
              value={sensitivity.attire as Attire}
              options={ATTIRE_OPTIONS}
              onChange={(v) => setSensitivity((s) => ({ ...s, attire: v }))}
            />
          </div>

          {/* --------------------------------------------------------------- glossary */}
          <div className={!isDraft ? "opacity-60" : ""}>
            <div className="flex items-center justify-between mb-2">
              <label className="block text-xs font-semibold text-[var(--ivory)] uppercase tracking-wider">
                Cultural glossary
              </label>
              {!isDraft && <LockedNote />}
            </div>
            <div className="flex flex-wrap gap-1.5 mb-2">
              {glossary.map((term) => (
                <span
                  key={term}
                  className="text-xs px-2.5 py-1 rounded-full bg-white/5 border border-white/10 text-[var(--ivory)] flex items-center gap-1.5"
                >
                  <span>{term}</span>
                  {isDraft && (
                    <button type="button" onClick={() => removeGlossaryTerm(term)} className="hover:text-[var(--danger)]">
                      <X className="w-3 h-3" />
                    </button>
                  )}
                </span>
              ))}
            </div>
            {isDraft && (
              <div className="flex gap-2">
                <input
                  value={glossaryDraft}
                  onChange={(e) => setGlossaryDraft(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && addGlossaryTerm()}
                  placeholder="+ Add a term (e.g. haldi, nikah, keynote)"
                  className="flex-1 text-xs px-3 py-1.5 rounded-full bg-black/40 border border-white/10 text-[var(--ivory)] placeholder:text-[var(--ink-faint)] focus:border-[var(--accent)] focus:outline-none"
                />
                <button
                  type="button"
                  onClick={addGlossaryTerm}
                  className="btn-secondary px-3 py-1.5 text-xs font-semibold flex items-center gap-1 shrink-0"
                >
                  <Plus className="w-3.5 h-3.5" />
                  <span>Add</span>
                </button>
              </div>
            )}
          </div>

          {/* --------------------------------------------------------------- vip topology */}
          <div className={!isDraft ? "opacity-60" : ""}>
            <div className="flex items-center justify-between mb-2">
              <label className="block text-xs font-semibold text-[var(--ivory)] uppercase tracking-wider">
                VIP topology
              </label>
              {!isDraft && <LockedNote />}
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
              <TopologyCard
                active={vipTopology === "pyramid"}
                title="Pyramid"
                body="Most guests are guests; you promote a few to VIP."
                disabled={!isDraft}
                onClick={() => setVipTopology("pyramid")}
              />
              <TopologyCard
                active={vipTopology === "flat"}
                title="Flat"
                body="Everyone is inner circle by default."
                disabled={!isDraft}
                onClick={() => setVipTopology("flat")}
              />
            </div>
          </div>

          {error && (
            <p className="text-xs text-[var(--danger)] p-3 rounded-xl bg-[var(--danger)]/10 border border-[var(--danger)]/20">
              {error}
            </p>
          )}

          <div className="flex items-center gap-3 pt-2 border-t border-white/10">
            <button
              type="button"
              disabled={saving}
              onClick={() => void handleSave()}
              className="btn-primary px-6 py-2.5 text-xs font-semibold disabled:opacity-50"
            >
              {saving ? "Saving…" : "Save settings"}
            </button>
            {savedAt && <span className="text-xs text-[var(--ok)] font-medium">Saved</span>}
          </div>
        </div>
      )}
    </section>
  );
}

function LockedNote() {
  return (
    <span className="flex items-center gap-1 text-[10px] font-semibold uppercase tracking-wider text-[var(--ink-faint)]">
      <Lock className="w-3 h-3" />
      <span>Locked after go-live</span>
    </span>
  );
}

function DialRow<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: T;
  options: { value: T; label: string }[];
  onChange: (v: T) => void;
}) {
  return (
    <div>
      <p className="text-[11px] text-[var(--ink-muted)] font-medium mb-1.5">{label}</p>
      <div className="inline-flex flex-wrap gap-1 p-1 rounded-xl bg-black/40 border border-white/10">
        {options.map((o) => (
          <button
            key={o.value}
            type="button"
            onClick={() => onChange(o.value)}
            aria-pressed={value === o.value}
            className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors ${
              value === o.value ? "bg-[var(--accent)] text-black" : "text-[var(--ink-muted)] hover:text-[var(--ivory)]"
            }`}
          >
            {o.label}
          </button>
        ))}
      </div>
    </div>
  );
}

function TopologyCard({
  active,
  title,
  body,
  disabled,
  onClick,
}: {
  active: boolean;
  title: string;
  body: string;
  disabled?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-pressed={active}
      className={`text-left p-3.5 rounded-xl transition-all border flex flex-col gap-1 disabled:opacity-60 ${
        active
          ? "bg-[var(--bg-2)] border-2 border-[var(--accent)]"
          : "bg-[var(--bg-1)]/60 border-white/5 hover:border-white/20"
      }`}
    >
      <span className="font-semibold text-xs text-[var(--ivory)]">{title}</span>
      <span className="text-[11px] text-[var(--ink-muted)] leading-relaxed">{body}</span>
    </button>
  );
}
