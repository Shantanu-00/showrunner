"use client";

import { useEffect, useState } from "react";
import {
  Sparkles,
  ArrowRight,
  ArrowLeft,
  Copy,
  Check,
  ShieldCheck,
  KeyRound,
  Globe,
  Users,
  DoorOpen,
  Lock,
  Calendar,
  Plus,
  AlertTriangle,
  SkipForward,
} from "lucide-react";
import { ensureAnonymousAuth } from "@/lib/firebase";
import { createEvent, parseItinerary, saveStages } from "@/lib/hostApi";
import { ApiError } from "@/lib/api";
import type {
  CreateEventResponse,
  ExpectedSetting,
  RequiredMoment,
  StageTheme,
} from "@/lib/hostTypes";
import { dateForDayIndex, dayIndexFromLocalDate, formatLocalDate, slugify } from "@/lib/hostTypes";
import type { PersonDoc } from "@/lib/types";
import { listenPeople } from "@/lib/firestore";
import { GoogleUpgradeCard } from "./GoogleUpgradeCard";
import { HostReturnPanel } from "./HostReturnPanel";
import { rememberEvent } from "./rememberedEvents";
import { ItineraryInputTabs, type ItineraryParsePayload } from "./ItineraryInputTabs";
import { StageEditorCard } from "./StageEditorCard";
import { HostJoinQr } from "./HostJoinQr";
import { PersonEnrollForm } from "./PersonEnrollForm";

type Step = 1 | 2 | 3 | 4 | 5;

const STEP_LABELS: Record<Step, string> = {
  1: "Details",
  2: "Itinerary",
  3: "Review",
  4: "People",
  5: "Links",
};

interface DraftStage {
  key: string;
  stageId: string;
  label: string;
  timeHint?: string;
  startsAt: string;
  endsAt: string;
  requiredMoments: RequiredMoment[];
  expectedSetting: ExpectedSetting | "";
  theme: StageTheme | "";
}

export function HostWizard() {
  const [step, setStep] = useState<Step>(1);

  // ---------------------------------------------------------------- step 1: details
  const [name, setName] = useState("");
  const [timezone, setTimezone] = useState(
    typeof Intl !== "undefined" ? Intl.DateTimeFormat().resolvedOptions().timeZone : "UTC"
  );
  const [useDates, setUseDates] = useState(false);
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [expectedParticipants, setExpectedParticipants] = useState("");
  const [accessMode, setAccessMode] = useState<"open" | "invite">("open");
  const [creating, setCreating] = useState(false);
  const [detailsError, setDetailsError] = useState<string | null>(null);
  const [created, setCreated] = useState<CreateEventResponse | null>(null);

  // ---------------------------------------------------------------- step 2: itinerary
  const [parsing, setParsing] = useState(false);
  const [parseError, setParseError] = useState<string | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);

  // ---------------------------------------------------------------- step 3: review timeline
  const [draftStages, setDraftStages] = useState<DraftStage[]>([]);
  const [savingStages, setSavingStages] = useState(false);
  const [stagesError, setStagesError] = useState<string | null>(null);

  const eventId = created?.eventId ?? null;

  function updateStage(key: string, patch: Partial<DraftStage>) {
    setDraftStages((prev) => prev.map((s) => (s.key === key ? { ...s, ...patch } : s)));
  }

  function addStage() {
    setDraftStages((prev) => [
      ...prev,
      {
        key: `new-${Date.now()}`,
        stageId: "",
        label: "New Stage",
        startsAt: "",
        endsAt: "",
        requiredMoments: [],
        expectedSetting: "",
        theme: "",
      },
    ]);
  }

  function removeStage(key: string) {
    setDraftStages((prev) => prev.filter((s) => s.key !== key));
  }

  function addMoment(key: string, label: string) {
    if (!label.trim()) return;
    updateStage(key, {
      requiredMoments: [
        ...(draftStages.find((s) => s.key === key)?.requiredMoments ?? []),
        { momentId: slugify(label), label: label.trim() },
      ],
    });
  }

  function removeMoment(key: string, momentId: string) {
    const stage = draftStages.find((s) => s.key === key);
    if (!stage) return;
    updateStage(key, { requiredMoments: stage.requiredMoments.filter((m) => m.momentId !== momentId) });
  }

  async function submitDetails() {
    // Already created (the host stepped back to fix a typo and forward again) — the event is a
    // one-shot creation, not something this step re-submits. Details can still be corrected from
    // the console's own affordances afterward; this step just moves on.
    if (created) {
      setStep(2);
      return;
    }
    if (!name.trim() || !timezone.trim()) {
      setDetailsError("Event name and timezone are required.");
      return;
    }
    if (useDates) {
      if (!startDate || !endDate) {
        setDetailsError("Enter both a start and an end date, or leave both blank.");
        return;
      }
      if (endDate < startDate) {
        setDetailsError("The end date is before the start date.");
        return;
      }
    }
    const participants = expectedParticipants.trim() ? Number.parseInt(expectedParticipants, 10) : null;
    if (expectedParticipants.trim() && (!Number.isFinite(participants) || (participants ?? 0) < 1)) {
      setDetailsError("Expected participants has to be a whole number, 1 or more.");
      return;
    }
    setCreating(true);
    setDetailsError(null);
    try {
      await ensureAnonymousAuth();
      const body: Parameters<typeof createEvent>[0] = { name: name.trim(), timezone: timezone.trim() };
      if (useDates) {
        body.startDate = startDate;
        body.endDate = endDate;
      }
      if (participants) body.expectedParticipants = participants;
      if (accessMode === "invite") body.accessMode = "invite";
      const res = await createEvent(body);
      rememberEvent(res.eventId, name.trim());
      setCreated(res);
      setStep(2);
    } catch (err) {
      setDetailsError(
        err instanceof ApiError ? `Couldn't create the event (${err.status}): ${err.message}` : "Something went wrong."
      );
    } finally {
      setCreating(false);
    }
  }

  async function handleParse(payload: ItineraryParsePayload) {
    if (!eventId) return;
    setParsing(true);
    setParseError(null);
    try {
      const out = await parseItinerary(eventId, payload);
      setDraftStages(
        out.stages.map((s, i) => ({
          key: `${s.stageId}-${i}-${Date.now()}`,
          stageId: s.stageId,
          label: s.label,
          timeHint: s.timeHint,
          startsAt: s.proposedStartLocal || "",
          endsAt: s.proposedEndLocal || "",
          requiredMoments: s.requiredMoments,
          expectedSetting: s.expectedSetting ?? "",
          theme: "",
        }))
      );
      setWarnings(out.warnings);
      setStep(3);
    } catch (err) {
      setParseError(
        err instanceof ApiError ? `Couldn't parse that (${err.status}): ${err.message}` : "Something went wrong."
      );
    } finally {
      setParsing(false);
    }
  }

  async function proceedFromReview() {
    if (draftStages.length === 0 || !eventId) {
      setStep(4);
      return;
    }
    setSavingStages(true);
    setStagesError(null);
    try {
      const payload = draftStages.map((s) => ({
        stageId: s.stageId || slugify(s.label),
        label: s.label,
        startsAt: s.startsAt ? new Date(s.startsAt).toISOString() : null,
        endsAt: s.endsAt ? new Date(s.endsAt).toISOString() : null,
        requiredMoments: s.requiredMoments,
        theme: s.theme || null,
        expectedSetting: s.expectedSetting || null,
      }));
      await saveStages(eventId, payload);
      setStep(4);
    } catch (err) {
      setStagesError(
        err instanceof ApiError ? `Couldn't save (${err.status}): ${err.message}` : "Something went wrong."
      );
    } finally {
      setSavingStages(false);
    }
  }

  function goBack() {
    setStep((s) => (s > 1 ? ((s - 1) as Step) : s));
  }

  return (
    <>
      {step === 1 && !created && <HostReturnPanel />}

      <div className="max-w-2xl mx-auto px-5 py-12">
        <div className="text-center mb-8">
          <div className="flex items-center justify-center gap-2 mb-2">
            <span className="p-1.5 rounded-lg bg-[var(--gold-500)]/15 text-[var(--accent)] border border-[var(--gold-500)]/20">
              <Sparkles className="w-4 h-4" />
            </span>
            <span className="font-mono text-xs uppercase tracking-[0.2em] font-semibold text-[var(--accent)]">
              NEW EVENT SETUP
            </span>
          </div>
          <h1 className="font-[family-name:var(--font-display)] text-3xl font-semibold text-gold-gradient mb-2">
            Create a Showrunner Event
          </h1>
          <p className="text-xs text-[var(--ink-muted)] max-w-md mx-auto">
            Any trip, party, or gathering — tell it your itinerary and it directs the rest.
          </p>
        </div>

        <Stepper step={step} onJump={setStep} />

        <div className="mt-8 space-y-6">
          {step === 1 && (
            <DetailsStep
              name={name}
              setName={setName}
              timezone={timezone}
              setTimezone={setTimezone}
              useDates={useDates}
              setUseDates={setUseDates}
              startDate={startDate}
              setStartDate={setStartDate}
              endDate={endDate}
              setEndDate={setEndDate}
              expectedParticipants={expectedParticipants}
              setExpectedParticipants={setExpectedParticipants}
              accessMode={accessMode}
              setAccessMode={setAccessMode}
              locked={Boolean(created)}
              error={detailsError}
              busy={creating}
              onNext={() => void submitDetails()}
            />
          )}

          {step === 2 && (
            <ItineraryStep
              parsing={parsing}
              error={parseError}
              onParse={(p) => void handleParse(p)}
              onSkip={() => setStep(3)}
              onBack={goBack}
            />
          )}

          {step === 3 && (
            <ReviewStep
              stages={draftStages}
              warnings={warnings}
              saving={savingStages}
              error={stagesError}
              onAddStage={addStage}
              onRemoveStage={removeStage}
              onUpdateStage={updateStage}
              onAddMoment={addMoment}
              onRemoveMoment={removeMoment}
              startsOn={useDates ? startDate : null}
              onBack={goBack}
              onNext={() => void proceedFromReview()}
            />
          )}

          {step === 4 && <PeopleStep eventId={eventId} onBack={goBack} onNext={() => setStep(5)} />}

          {step === 5 && created && <LinksStep created={created} eventId={created.eventId} />}
        </div>
      </div>
    </>
  );
}

// =============================================================================================
// stepper

function Stepper({ step, onJump }: { step: Step; onJump: (s: Step) => void }) {
  const steps: Step[] = [1, 2, 3, 4, 5];
  return (
    <div className="flex items-center justify-between gap-1" role="tablist" aria-label="Event setup steps">
      {steps.map((s, i) => {
        // Only steps already visited are jumpable — a future step's inputs don't exist yet (there
        // is nothing to review at Step 3 before Step 2 has produced it), so it renders inert rather
        // than clickable-but-silently-ignored.
        const reachable = s <= step;
        const active = s === step;
        return (
          <div key={s} className="flex items-center flex-1 last:flex-none">
            <button
              type="button"
              role="tab"
              aria-selected={active}
              aria-current={active ? "step" : undefined}
              disabled={!reachable}
              onClick={() => reachable && onJump(s)}
              className={`flex flex-col items-center gap-1.5 group ${reachable ? "cursor-pointer" : "cursor-default"}`}
            >
              <span
                className={`w-7 h-7 rounded-full flex items-center justify-center text-xs font-mono font-bold transition-all ${
                  active
                    ? "bg-[var(--accent)] text-black shadow-md scale-110"
                    : reachable
                      ? "bg-white/10 text-[var(--ivory)] border border-white/15 group-hover:border-[var(--accent)]"
                      : "bg-white/5 text-[var(--ink-faint)] border border-white/5"
                }`}
              >
                {s}
              </span>
              <span
                className={`text-[10px] font-semibold uppercase tracking-wider whitespace-nowrap ${
                  active ? "text-[var(--accent)]" : "text-[var(--ink-faint)]"
                }`}
              >
                {STEP_LABELS[s]}
              </span>
            </button>
            {i < steps.length - 1 && (
              <div className={`flex-1 h-px mx-1 ${s < step ? "bg-[var(--accent)]/50" : "bg-white/10"}`} />
            )}
          </div>
        );
      })}
    </div>
  );
}

// =============================================================================================
// step 1 — details

function DetailsStep({
  name,
  setName,
  timezone,
  setTimezone,
  useDates,
  setUseDates,
  startDate,
  setStartDate,
  endDate,
  setEndDate,
  expectedParticipants,
  setExpectedParticipants,
  accessMode,
  setAccessMode,
  locked,
  error,
  busy,
  onNext,
}: {
  name: string;
  setName: (v: string) => void;
  timezone: string;
  setTimezone: (v: string) => void;
  useDates: boolean;
  setUseDates: (v: boolean) => void;
  startDate: string;
  setStartDate: (v: string) => void;
  endDate: string;
  setEndDate: (v: string) => void;
  expectedParticipants: string;
  setExpectedParticipants: (v: string) => void;
  accessMode: "open" | "invite";
  setAccessMode: (v: "open" | "invite") => void;
  locked: boolean;
  error: string | null;
  busy: boolean;
  onNext: () => void;
}) {
  return (
    <div className="space-y-6" onKeyDown={(e) => e.key === "Enter" && !locked && onNext()}>
      {locked && (
        <p className="text-xs text-[var(--ok)] bg-[var(--ok)]/10 border border-[var(--ok)]/20 rounded-xl px-4 py-2.5">
          This event has been created — these details are locked. Edit them from the console after setup.
        </p>
      )}

      <div className="glass-card p-6 rounded-3xl border border-white/10 space-y-4">
        <div>
          <label className="block text-xs font-semibold text-[var(--ivory)] uppercase tracking-wider mb-2">
            Event Name
          </label>
          <input
            type="text"
            value={name}
            disabled={locked}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. Japan trip with the crew"
            className="w-full px-4 py-3 rounded-xl bg-black/40 border border-white/10 text-sm text-[var(--ivory)] placeholder:text-[var(--ink-faint)] focus:border-[var(--accent)] focus:outline-none transition-colors disabled:opacity-60"
          />
        </div>

        <div>
          <label className="block text-xs font-semibold text-[var(--ivory)] uppercase tracking-wider mb-2">
            <span className="flex items-center gap-1.5">
              <Globe className="w-3.5 h-3.5" />
              <span>Timezone</span>
            </span>
          </label>
          <input
            type="text"
            value={timezone}
            disabled={locked}
            onChange={(e) => setTimezone(e.target.value)}
            className="w-full px-4 py-3 rounded-xl bg-black/40 border border-white/10 text-sm font-mono text-[var(--ivory)] focus:border-[var(--accent)] focus:outline-none transition-colors disabled:opacity-60"
          />
          <p className="text-[11px] text-[var(--ink-faint)] mt-1.5">
            Auto-filled from your browser. Photo timestamps are interpreted through this.
          </p>
        </div>

        <div>
          <label className="flex items-center gap-2 mb-2">
            <input
              type="checkbox"
              checked={useDates}
              disabled={locked}
              onChange={(e) => setUseDates(e.target.checked)}
              className="w-4 h-4 rounded accent-[var(--accent)]"
            />
            <span className="text-xs font-semibold text-[var(--ivory)] uppercase tracking-wider flex items-center gap-1.5">
              <Calendar className="w-3.5 h-3.5" />
              <span>This event has dates</span>
            </span>
          </label>
          {useDates && (
            <div className="grid grid-cols-2 gap-3 mt-2">
              <input
                type="date"
                value={startDate}
                disabled={locked}
                onChange={(e) => setStartDate(e.target.value)}
                aria-label="Start date"
                className="px-4 py-3 rounded-xl bg-black/40 border border-white/10 text-sm text-[var(--ivory)] focus:border-[var(--accent)] focus:outline-none disabled:opacity-60"
              />
              <input
                type="date"
                value={endDate}
                disabled={locked}
                onChange={(e) => setEndDate(e.target.value)}
                aria-label="End date"
                min={startDate || undefined}
                className="px-4 py-3 rounded-xl bg-black/40 border border-white/10 text-sm text-[var(--ivory)] focus:border-[var(--accent)] focus:outline-none disabled:opacity-60"
              />
            </div>
          )}
          <p className="text-[11px] text-[var(--ink-faint)] mt-1.5">
            Optional — a multi-day trip gets "Day 1 / Day 2…" grouping everywhere. Skip it for a single
            party with no fixed calendar.
          </p>
        </div>

        <div>
          <label className="block text-xs font-semibold text-[var(--ivory)] uppercase tracking-wider mb-2">
            <span className="flex items-center gap-1.5">
              <Users className="w-3.5 h-3.5" />
              <span>Expected participants (optional)</span>
            </span>
          </label>
          <input
            type="number"
            min={1}
            inputMode="numeric"
            value={expectedParticipants}
            disabled={locked}
            onChange={(e) => setExpectedParticipants(e.target.value)}
            placeholder="How many people are coming?"
            className="w-full px-4 py-3 rounded-xl bg-black/40 border border-white/10 text-sm text-[var(--ivory)] placeholder:text-[var(--ink-faint)] focus:border-[var(--accent)] focus:outline-none disabled:opacity-60"
          />
        </div>
      </div>

      <div>
        <label className="block text-xs font-semibold text-[var(--ivory)] uppercase tracking-wider mb-3">
          Who can join
        </label>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
          <AccessCard
            active={accessMode === "open"}
            icon={DoorOpen}
            title="Open link"
            body="Anyone with the link joins — best for a venue with the QR on a wall."
            disabled={locked}
            onClick={() => setAccessMode("open")}
          />
          <AccessCard
            active={accessMode === "invite"}
            icon={Lock}
            title="Invite code"
            body="A code is required to join — best for a private group."
            disabled={locked}
            onClick={() => setAccessMode("invite")}
          />
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
        onClick={onNext}
        className="btn-primary w-full py-4 rounded-full text-sm font-semibold flex items-center justify-center gap-2 shadow-2xl disabled:opacity-50"
      >
        {busy ? (
          <span>Creating your event…</span>
        ) : (
          <>
            <span>{locked ? "Continue" : "Create Event & Continue"}</span>
            <ArrowRight className="w-4 h-4 stroke-[2.5]" />
          </>
        )}
      </button>
    </div>
  );
}

function AccessCard({
  active,
  icon: Icon,
  title,
  body,
  disabled,
  onClick,
}: {
  active: boolean;
  icon: React.ElementType;
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
      className={`text-left p-4 rounded-2xl transition-all border flex flex-col gap-1.5 disabled:opacity-60 ${
        active
          ? "bg-[var(--bg-2)] border-2 border-[var(--accent)] shadow-lg"
          : "bg-[var(--bg-1)]/60 border-white/5 hover:border-white/20"
      }`}
    >
      <span className="flex items-center justify-between gap-2">
        <span className="flex items-center gap-2 font-semibold text-sm text-[var(--ivory)]">
          <Icon className="w-4 h-4 text-[var(--accent)]" />
          <span>{title}</span>
        </span>
        {active && <Check className="w-4 h-4 stroke-[3] text-[var(--accent)]" />}
      </span>
      <span className="text-[11px] text-[var(--ink-muted)] leading-relaxed">{body}</span>
    </button>
  );
}

// =============================================================================================
// step 2 — itinerary

function ItineraryStep({
  parsing,
  error,
  onParse,
  onSkip,
  onBack,
}: {
  parsing: boolean;
  error: string | null;
  onParse: (p: ItineraryParsePayload) => void;
  onSkip: () => void;
  onBack: () => void;
}) {
  return (
    <div className="space-y-5">
      <div className="glass-card p-6 rounded-3xl border border-white/10 space-y-4">
        <div className="flex items-center gap-2 text-xs font-semibold text-[var(--gold-300)]">
          <Sparkles className="w-4 h-4" />
          <span>Give it your itinerary — paste, upload a PDF, or a screenshot</span>
        </div>
        <ItineraryInputTabs onParse={onParse} busy={parsing} />
        {error && <p className="text-xs text-[var(--danger)]">{error}</p>}
      </div>

      <div className="flex items-center justify-between pt-2">
        <button
          type="button"
          onClick={onBack}
          className="btn-secondary px-5 py-3 text-xs font-semibold flex items-center gap-1.5"
        >
          <ArrowLeft className="w-3.5 h-3.5" />
          <span>Back</span>
        </button>
        <button
          type="button"
          onClick={onSkip}
          className="text-xs font-semibold text-[var(--ink-muted)] hover:text-[var(--accent)] transition-colors flex items-center gap-1.5"
        >
          <SkipForward className="w-3.5 h-3.5" />
          <span>I&rsquo;ll add the timeline later</span>
        </button>
      </div>
    </div>
  );
}

// =============================================================================================
// step 3 — review timeline

function ReviewStep({
  stages,
  warnings,
  saving,
  error,
  onAddStage,
  onRemoveStage,
  onUpdateStage,
  onAddMoment,
  onRemoveMoment,
  startsOn,
  onBack,
  onNext,
}: {
  stages: DraftStage[];
  warnings: string[];
  saving: boolean;
  error: string | null;
  onAddStage: () => void;
  onRemoveStage: (key: string) => void;
  onUpdateStage: (key: string, patch: Partial<DraftStage>) => void;
  onAddMoment: (key: string, label: string) => void;
  onRemoveMoment: (key: string, momentId: string) => void;
  startsOn: string | null;
  onBack: () => void;
  onNext: () => void;
}) {
  return (
    <div className="space-y-5">
      {warnings.length > 0 && (
        <div className="p-3 rounded-xl bg-[var(--warn)]/15 border border-[var(--warn)]/30 text-xs text-[var(--warn)] space-y-1">
          {warnings.map((w, i) => (
            <div key={i} className="flex items-center gap-1.5">
              <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
              <span>{w}</span>
            </div>
          ))}
        </div>
      )}

      {stages.length === 0 ? (
        <div className="glass-card p-8 rounded-3xl border border-white/10 text-center">
          <p className="text-sm text-[var(--ivory)] mb-1">No stages yet</p>
          <p className="text-xs text-[var(--ink-muted)] mb-4">
            Add one manually, or go back and give it an itinerary to parse.
          </p>
          <button
            type="button"
            onClick={onAddStage}
            className="btn-secondary px-4 py-2.5 text-xs font-semibold inline-flex items-center gap-1.5"
          >
            <Plus className="w-4 h-4" />
            <span>Add a stage</span>
          </button>
        </div>
      ) : (
        <div className="space-y-6">{renderStages()}</div>
      )}

      {stages.length > 0 && (
        <button
          type="button"
          onClick={onAddStage}
          className="btn-secondary px-4 py-2.5 text-xs font-semibold flex items-center gap-1.5"
        >
          <Plus className="w-4 h-4" />
          <span>Add another stage</span>
        </button>
      )}

      {error && <p className="text-xs text-[var(--danger)]">{error}</p>}

      <div className="flex items-center justify-between pt-2">
        <button
          type="button"
          onClick={onBack}
          className="btn-secondary px-5 py-3 text-xs font-semibold flex items-center gap-1.5"
        >
          <ArrowLeft className="w-3.5 h-3.5" />
          <span>Back</span>
        </button>
        <button
          type="button"
          disabled={saving}
          onClick={onNext}
          className="btn-primary px-6 py-3 rounded-full text-xs font-semibold flex items-center gap-1.5 disabled:opacity-50"
        >
          <span>
            {saving ? "Saving timeline…" : stages.length === 0 ? "Skip" : "Save Timeline & Continue"}
          </span>
          <ArrowRight className="w-3.5 h-3.5" />
        </button>
      </div>
    </div>
  );

  function stageCard(stage: DraftStage, position: number) {
    return (
      <StageEditorCard
        key={stage.key}
        position={position}
        label={stage.label}
        timeHint={stage.timeHint}
        startsAt={stage.startsAt}
        endsAt={stage.endsAt}
        requiredMoments={stage.requiredMoments}
        expectedSetting={stage.expectedSetting}
        theme={stage.theme}
        canEdit
        onLabelChange={(v) => onUpdateStage(stage.key, { label: v })}
        onStartsAtChange={(v) => onUpdateStage(stage.key, { startsAt: v })}
        onEndsAtChange={(v) => onUpdateStage(stage.key, { endsAt: v })}
        onExpectedSettingChange={(v) => onUpdateStage(stage.key, { expectedSetting: v })}
        onThemeChange={(v) => onUpdateStage(stage.key, { theme: v })}
        onAddMoment={(label) => onAddMoment(stage.key, label)}
        onRemoveMoment={(momentId) => onRemoveMoment(stage.key, momentId)}
        onRemove={() => onRemoveStage(stage.key)}
      />
    );
  }

  /** Day-grouped rows under "Day N — <date>" headers, derived from each stage's own picker value
   * against the event's declared start date — the same convention `ItineraryPanel` uses for the
   * identical grouping once a timeline is saved. An undated event (no start date entered in Step 1)
   * renders the flat list it always did; the numbering badge tracks the stage's position in the
   * *unsorted* array so it matches the order `PUT /stages` will save, not the day it's grouped under. */
  function renderStages() {
    const positionOf = new Map(stages.map((s, i) => [s.key, i + 1]));
    if (!startsOn) {
      return <div className="space-y-4">{stages.map((s) => stageCard(s, positionOf.get(s.key) ?? 1))}</div>;
    }
    const byDay = new Map<number | null, DraftStage[]>();
    for (const s of stages) {
      const idx = dayIndexFromLocalDate(startsOn, s.startsAt);
      const arr = byDay.get(idx) ?? [];
      arr.push(s);
      byDay.set(idx, arr);
    }
    const keys = Array.from(byDay.keys()).sort((a, b) => {
      if (a === null) return 1;
      if (b === null) return -1;
      return a - b;
    });
    return (
      <>
        {keys.map((dayIndex) => (
          <div key={String(dayIndex)} className="space-y-4">
            <div className="flex items-center gap-2 pt-1">
              <Calendar className="w-3.5 h-3.5 text-[var(--gold-300)]" />
              <h4 className="text-xs font-semibold uppercase tracking-wider text-[var(--gold-300)]">
                {dayIndex !== null
                  ? `Day ${dayIndex} — ${formatLocalDate(dateForDayIndex(startsOn, dayIndex))}`
                  : "Unscheduled"}
              </h4>
            </div>
            <div className="space-y-4">
              {(byDay.get(dayIndex) as DraftStage[]).map((s) => stageCard(s, positionOf.get(s.key) ?? 1))}
            </div>
          </div>
        ))}
      </>
    );
  }
}

// =============================================================================================
// step 4 — people

function PeopleStep({
  eventId,
  onBack,
  onNext,
}: {
  eventId: string | null;
  onBack: () => void;
  onNext: () => void;
}) {
  const [equalFeatured, setEqualFeatured] = useState(false);
  const [people, setPeople] = useState<PersonDoc[]>([]);
  const [peopleError, setPeopleError] = useState<string | null>(null);

  useEffect(() => {
    if (!eventId) return;
    return listenPeople(eventId, setPeople, () => setPeopleError("Couldn't load the people you've added."));
  }, [eventId]);

  // Step 4 only ever renders once the event exists (it comes after Step 1's creation), but a
  // missing eventId is still handled rather than assumed away — the form has nowhere to send a
  // photo without one.
  if (!eventId) {
    return (
      <div className="space-y-5">
        <div className="glass-card p-8 rounded-3xl border border-white/10 text-center">
          <p className="text-sm text-[var(--ink-muted)]">
            Create the event on Step 1 before adding people.
          </p>
        </div>
        <StepNav onBack={onBack} onNext={onNext} nextLabel="Skip" />
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <div className="glass-card p-5 rounded-3xl border border-white/10">
        <label className="flex items-center justify-between gap-3 cursor-pointer">
          <span className="min-w-0">
            <span className="block text-xs font-semibold text-[var(--ivory)] uppercase tracking-wider mb-1">
              Everyone is equally featured
            </span>
            <span className="block text-[11px] text-[var(--ink-muted)] leading-relaxed">
              On: people you add default to inner circle. Off: they default to guest. Either way,
              you can change any single person&rsquo;s tier later from the host console.
            </span>
          </span>
          <input
            type="checkbox"
            checked={equalFeatured}
            onChange={(e) => setEqualFeatured(e.target.checked)}
            className="w-5 h-5 rounded accent-[var(--accent)] shrink-0"
          />
        </label>
      </div>

      <PersonEnrollForm eventId={eventId} defaultTier={equalFeatured ? 1 : 3} />

      {peopleError && <p className="text-xs text-[var(--danger)]">{peopleError}</p>}

      {people.length > 0 && (
        <div className="glass-card p-5 rounded-3xl border border-white/10">
          <p className="text-xs font-semibold text-[var(--ivory)] uppercase tracking-wider mb-3">
            Added so far ({people.length})
          </p>
          <div className="flex flex-wrap gap-2">
            {people.map((p) => (
              <span
                key={p.personId}
                className="text-xs px-3 py-1.5 rounded-full bg-white/5 border border-white/10 text-[var(--ivory)]"
              >
                {p.displayName || "Unnamed"}
              </span>
            ))}
          </div>
        </div>
      )}

      <StepNav onBack={onBack} onNext={onNext} nextLabel={people.length > 0 ? "Continue" : "Skip"} />
    </div>
  );
}

function StepNav({
  onBack,
  onNext,
  nextLabel,
}: {
  onBack: () => void;
  onNext: () => void;
  nextLabel: string;
}) {
  return (
    <div className="flex items-center justify-between pt-2">
      <button
        type="button"
        onClick={onBack}
        className="btn-secondary px-5 py-3 text-xs font-semibold flex items-center gap-1.5"
      >
        <ArrowLeft className="w-3.5 h-3.5" />
        <span>Back</span>
      </button>
      <button
        type="button"
        onClick={onNext}
        className="btn-primary px-6 py-3 rounded-full text-xs font-semibold flex items-center gap-1.5"
      >
        <span>{nextLabel}</span>
        <ArrowRight className="w-3.5 h-3.5" />
      </button>
    </div>
  );
}

// =============================================================================================
// step 5 — links

function LinksStep({ created, eventId }: { created: CreateEventResponse; eventId: string }) {
  const [copiedCode, setCopiedCode] = useState(false);
  const [copiedLink, setCopiedLink] = useState(false);
  const [copiedJoin, setCopiedJoin] = useState(false);

  const joinUrl =
    typeof window !== "undefined"
      ? `${window.location.origin}/join/${eventId}${created.joinCode ? `?joinCode=${created.joinCode}` : ""}`
      : `/join/${eventId}`;

  return (
    <div className="space-y-4 animate-fadeIn">
      <div className="text-center mb-2">
        <div className="w-14 h-14 rounded-full bg-[var(--gold-500)]/15 text-[var(--accent)] flex items-center justify-center mx-auto mb-3 border border-[var(--gold-500)]/30">
          <ShieldCheck className="w-7 h-7 stroke-[2]" />
        </div>
        <h2 className="font-[family-name:var(--font-display)] text-2xl font-semibold text-gold-gradient mb-1">
          Your event is ready
        </h2>
        <p className="text-xs text-[var(--ink-muted)]">
          Save these before you leave this screen — some of them can&rsquo;t be shown again.
        </p>
      </div>

      <CopyCard
        icon={KeyRound}
        title="Host Recovery Code"
        value={created.recoveryCode}
        copied={copiedCode}
        onCopy={() => {
          void navigator.clipboard.writeText(created.recoveryCode);
          setCopiedCode(true);
          setTimeout(() => setCopiedCode(false), 2000);
        }}
        note="Write this down — it's the only way back in if you lose every device that's signed in."
        mono
      />

      <CopyCard
        icon={Globe}
        title="Co-Host Access Link"
        value={created.hostLink}
        copied={copiedLink}
        onCopy={() => {
          void navigator.clipboard.writeText(created.hostLink);
          setCopiedLink(true);
          setTimeout(() => setCopiedLink(false), 2000);
        }}
        small
      />

      {created.joinCode && (
        <CopyCard
          icon={KeyRound}
          title="Invite Code"
          value={created.joinCode}
          copied={copiedJoin}
          onCopy={() => {
            void navigator.clipboard.writeText(created.joinCode as string);
            setCopiedJoin(true);
            setTimeout(() => setCopiedJoin(false), 2000);
          }}
          note="Shown once — only a fingerprint of it is stored. Rotate it from the console if it's lost."
          mono
        />
      )}

      <div className="rounded-2xl glass-card p-5 border border-white/10 shadow-lg flex flex-col sm:flex-row items-center gap-4">
        <HostJoinQr url={joinUrl} sizePx={140} />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1.5 text-xs font-semibold text-[var(--ivory)]">
            <Users className="w-4 h-4 text-[var(--accent)]" />
            <span>Guest Join Link</span>
          </div>
          <p className="text-xs text-[var(--ink-muted)] leading-relaxed mb-2">
            Scan or share this so guests can start uploading.
          </p>
          <p className="font-mono text-[11px] break-all text-[var(--ivory-dim)] bg-black/40 p-2.5 rounded-lg border border-white/5">
            {joinUrl}
          </p>
        </div>
      </div>

      <GoogleUpgradeCard />

      <a
        href={`/host/${eventId}`}
        className="btn-primary w-full py-4 rounded-full flex items-center justify-center gap-2 text-sm font-semibold shadow-2xl"
      >
        <span>Open Host Console</span>
        <ArrowRight className="w-4 h-4 stroke-[2.5]" />
      </a>
    </div>
  );
}

function CopyCard({
  icon: Icon,
  title,
  value,
  copied,
  onCopy,
  note,
  mono,
  small,
}: {
  icon: React.ElementType;
  title: string;
  value: string;
  copied: boolean;
  onCopy: () => void;
  note?: string;
  mono?: boolean;
  small?: boolean;
}) {
  return (
    <div className="rounded-2xl glass-card p-5 border border-white/10 shadow-lg">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2 text-xs font-semibold text-[var(--gold-300)]">
          <Icon className="w-4 h-4" />
          <span>{title}</span>
        </div>
        <button
          type="button"
          onClick={onCopy}
          className="flex items-center gap-1 text-[11px] px-2.5 py-1 rounded-md bg-white/5 hover:bg-white/15 text-white"
        >
          {copied ? <Check className="w-3 h-3 text-[var(--ok)]" /> : <Copy className="w-3 h-3" />}
          <span>{copied ? "Copied" : "Copy"}</span>
        </button>
      </div>
      <p
        className={`${mono ? "font-mono" : ""} ${small ? "text-xs" : "text-base"} break-all text-[var(--ivory)] bg-black/40 p-3 rounded-xl border border-white/5`}
      >
        {value}
      </p>
      {note && <p className="text-[11px] text-[var(--warn)] mt-2 leading-relaxed">{note}</p>}
    </div>
  );
}
