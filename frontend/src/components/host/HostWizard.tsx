"use client";

import { useEffect, useState } from "react";
import {
  Sparkles,
  ArrowRight,
  ArrowLeft,
  ShieldCheck,
  KeyRound,
  Globe,
  Users,
  DoorOpen,
  Lock,
  Calendar,
  Plus,
  AlertTriangle,
  UserPlus,
} from "lucide-react";
import { ensureAnonymousAuth, refreshClaims } from "@/lib/firebase";
import { createEvent, extractItinerary, parseItinerary, saveStages } from "@/lib/hostApi";
import { ApiError } from "@/lib/api";
import type {
  CreateEventResponse,
  ExpectedSetting,
  ParsedPerson,
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

type Step = 1 | 2 | 3 | 4;

const STEP_LABELS: Record<Step, string> = {
  1: "Setup",
  2: "Timeline",
  3: "People",
  4: "Launch",
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

interface ExtractedSummary {
  name: string;
  stagesCount: number;
  peopleCount: number;
  dates?: string;
  timezone?: string;
}

export function HostWizard() {
  const [step, setStep] = useState<Step>(1);

  // ---------------------------------------------------------------- step 1: details & ai extraction
  const [name, setName] = useState("");
  const [timezone, setTimezone] = useState(
    typeof Intl !== "undefined" ? Intl.DateTimeFormat().resolvedOptions().timeZone : "UTC"
  );
  const [useDates, setUseDates] = useState(false);
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [expectedParticipants, setExpectedParticipants] = useState("");
  const [accessMode, setAccessMode] = useState<"open" | "invite">("open");
  const [extracting, setExtracting] = useState(false);
  const [extractError, setExtractError] = useState<string | null>(null);
  const [extractedSummary, setExtractedSummary] = useState<ExtractedSummary | null>(null);
  const [suggestedPeople, setSuggestedPeople] = useState<ParsedPerson[]>([]);

  const [creating, setCreating] = useState(false);
  const [detailsError, setDetailsError] = useState<string | null>(null);
  const [created, setCreated] = useState<CreateEventResponse | null>(null);

  // ---------------------------------------------------------------- step 2: review timeline
  const [draftStages, setDraftStages] = useState<DraftStage[]>([]);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [savingStages, setSavingStages] = useState(false);
  const [stagesError, setStagesError] = useState<string | null>(null);
  const [reparsing, setReparsing] = useState(false);
  const [reparseError, setReparseError] = useState<string | null>(null);

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

  // Handle Step 1 AI Extraction
  async function handleExtractStep1(payload: ItineraryParsePayload) {
    setExtracting(true);
    setExtractError(null);
    try {
      await ensureAnonymousAuth();
      const out = await extractItinerary(payload);

      if (out.suggestedName) {
        setName(out.suggestedName);
      }
      if (out.timezone) {
        setTimezone(out.timezone);
      }
      if (out.startDate && out.endDate) {
        setUseDates(true);
        setStartDate(out.startDate);
        setEndDate(out.endDate);
      } else if (out.startDate) {
        setUseDates(true);
        setStartDate(out.startDate);
        setEndDate(out.startDate);
      }
      if (out.expectedParticipants && out.expectedParticipants > 0) {
        setExpectedParticipants(String(out.expectedParticipants));
      }
      if (out.suggestedAccessMode) {
        setAccessMode(out.suggestedAccessMode);
      }
      if (out.suggestedPeople && out.suggestedPeople.length > 0) {
        setSuggestedPeople(out.suggestedPeople);
      }

      if (out.stages && out.stages.length > 0) {
        setDraftStages(
          out.stages.map((s, i) => ({
            key: `${s.stageId || "stage"}-${i}-${Date.now()}`,
            stageId: s.stageId,
            label: s.label,
            timeHint: s.timeHint,
            startsAt: s.proposedStartLocal || "",
            endsAt: s.proposedEndLocal || "",
            requiredMoments: s.requiredMoments || [],
            expectedSetting: (s.expectedSetting as ExpectedSetting) || "",
            theme: (s.theme as StageTheme) || "",
          }))
        );
      }

      setWarnings(out.warnings || []);
      setExtractedSummary({
        name: out.suggestedName || "Event",
        stagesCount: out.stages?.length || 0,
        peopleCount: out.suggestedPeople?.length || 0,
        dates: out.startDate && out.endDate ? `${out.startDate} to ${out.endDate}` : undefined,
        timezone: out.timezone || undefined,
      });
    } catch (err) {
      setExtractError(
        err instanceof ApiError ? `AI extraction failed (${err.status}): ${err.message}` : "Couldn't parse that itinerary."
      );
    } finally {
      setExtracting(false);
    }
  }

  // Step 1 Submission: create the event
  async function submitDetails() {
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
      await refreshClaims();
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

  // Handle re-parse on Step 2
  async function handleReparse(payload: ItineraryParsePayload) {
    if (!eventId) return;
    setReparsing(true);
    setReparseError(null);
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
          theme: (s.theme as StageTheme) || "",
        }))
      );
      if (out.suggestedPeople && out.suggestedPeople.length > 0) {
        setSuggestedPeople(out.suggestedPeople);
      }
      setWarnings(out.warnings);
    } catch (err) {
      setReparseError(
        err instanceof ApiError ? `Couldn't parse that (${err.status}): ${err.message}` : "Something went wrong."
      );
    } finally {
      setReparsing(false);
    }
  }

  // Step 2 Submission: save stages
  async function proceedFromTimeline() {
    if (draftStages.length === 0 || !eventId) {
      setStep(3);
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
      setStep(3);
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
              AI-POWERED EVENT SETUP
            </span>
          </div>
          <h1 className="font-[family-name:var(--font-display)] text-3xl font-semibold text-gold-gradient mb-2">
            Create a Showrunner Event
          </h1>
          <p className="text-xs text-[var(--ink-muted)] max-w-md mx-auto">
            Drop your itinerary, PDF, screenshot, or trip notes — Gemini 3.7 Flash directs the rest.
          </p>
        </div>

        <Stepper step={step} onJump={setStep} />

        <div className="mt-8 space-y-6">
          {step === 1 && (
            <SetupStep
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
              extracting={extracting}
              extractError={extractError}
              extractedSummary={extractedSummary}
              onExtract={(p) => void handleExtractStep1(p)}
              locked={Boolean(created)}
              error={detailsError}
              busy={creating}
              onNext={() => void submitDetails()}
            />
          )}

          {step === 2 && (
            <TimelineStep
              stages={draftStages}
              warnings={warnings}
              saving={savingStages}
              error={stagesError}
              reparsing={reparsing}
              reparseError={reparseError}
              onReparse={(p) => void handleReparse(p)}
              onAddStage={addStage}
              onRemoveStage={removeStage}
              onUpdateStage={updateStage}
              onAddMoment={addMoment}
              onRemoveMoment={removeMoment}
              startsOn={useDates ? startDate : null}
              onBack={goBack}
              onNext={() => void proceedFromTimeline()}
            />
          )}

          {step === 3 && (
            <PeopleStep
              eventId={eventId}
              suggestedPeople={suggestedPeople}
              onBack={goBack}
              onNext={() => setStep(4)}
            />
          )}

          {step === 4 && created && <LaunchStep created={created} eventId={created.eventId} />}
        </div>
      </div>
    </>
  );
}

// =============================================================================================
// stepper

function Stepper({ step, onJump }: { step: Step; onJump: (s: Step) => void }) {
  const steps: Step[] = [1, 2, 3, 4];
  return (
    <div className="flex items-center justify-between gap-1" role="tablist" aria-label="Event setup steps">
      {steps.map((s, i) => {
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
// step 1 — setup (AI-first + editable fields)

function SetupStep({
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
  extracting,
  extractError,
  extractedSummary,
  onExtract,
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
  extracting: boolean;
  extractError: string | null;
  extractedSummary: ExtractedSummary | null;
  onExtract: (p: ItineraryParsePayload) => void;
  locked: boolean;
  error: string | null;
  busy: boolean;
  onNext: () => void;
}) {
  const [showManual, setShowManual] = useState(false);

  return (
    <div className="space-y-6" onKeyDown={(e) => e.key === "Enter" && !locked && onNext()}>
      {locked && (
        <p className="text-xs text-[var(--ok)] bg-[var(--ok)]/10 border border-[var(--ok)]/20 rounded-xl px-4 py-2.5">
          This event has been created — these details are locked. Edit them from the console after setup.
        </p>
      )}

      {/* AI Extraction Dropzone */}
      {!locked && (
        <div className="glass-card p-6 rounded-3xl border border-[var(--gold-500)]/30 bg-gradient-to-b from-[var(--gold-500)]/10 to-transparent space-y-4">
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <div className="p-1.5 rounded-lg bg-[var(--gold-500)]/20 text-[var(--accent)]">
                <Sparkles className="w-4 h-4" />
              </div>
              <div>
                <h3 className="text-xs font-semibold uppercase tracking-wider text-[var(--gold-300)]">
                  Fast AI Setup with Gemini 3.7 Flash
                </h3>
                <p className="text-[11px] text-[var(--ink-muted)]">
                  Paste WhatsApp forward, upload PDF or screenshot — Gemini auto-fills everything.
                </p>
              </div>
            </div>
          </div>

          <ItineraryInputTabs
            onParse={onExtract}
            busy={extracting}
            buttonLabel="Extract Event with Gemini 3.7 Flash"
            placeholder="e.g. Japan Trip Oct 12-16 with 4 friends in Tokyo and Kyoto. Visiting Shibuya, Fushimi Inari, team izakaya dinner. Travelers: Alex, Maya, Ken, Sarah..."
          />

          {extractError && (
            <p className="text-xs text-[var(--danger)] p-2.5 rounded-xl bg-[var(--danger)]/10 border border-[var(--danger)]/20">
              {extractError}
            </p>
          )}

          {extractedSummary && (
            <div className="p-3 rounded-2xl bg-[var(--ok)]/15 border border-[var(--ok)]/30 text-xs text-[var(--ok)] flex flex-wrap items-center gap-2">
              <span className="font-semibold">✨ Auto-filled:</span>
              <span className="font-medium text-[var(--ivory)]">{extractedSummary.name}</span>
              <span>•</span>
              <span>{extractedSummary.stagesCount} stages extracted</span>
              {extractedSummary.peopleCount > 0 && (
                <>
                  <span>•</span>
                  <span>{extractedSummary.peopleCount} people detected</span>
                </>
              )}
              {extractedSummary.dates && (
                <>
                  <span>•</span>
                  <span>{extractedSummary.dates}</span>
                </>
              )}
            </div>
          )}
        </div>
      )}

      {/* Review & Edit Fields */}
      <div className="glass-card p-6 rounded-3xl border border-white/10 space-y-4">
        <div className="flex items-center justify-between pb-1 border-b border-white/5">
          <span className="text-xs font-semibold text-[var(--ivory)] uppercase tracking-wider">
            Event Information
          </span>
          {extractedSummary && (
            <span className="text-[10px] font-mono text-[var(--accent)] uppercase tracking-wider">
              AI-Prefilled
            </span>
          )}
        </div>

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
            Auto-detected or extracted from location. Photo timestamps are interpreted through this.
          </p>
        </div>

        <div>
          <label className="flex items-center gap-2 mb-2 cursor-pointer">
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
            body="A code is required to join — best for a private group or trip."
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
        disabled={busy || !name.trim()}
        onClick={onNext}
        className="btn-primary w-full py-4 rounded-full text-sm font-semibold flex items-center justify-center gap-2 shadow-2xl disabled:opacity-50"
      >
        {busy ? (
          <span>Creating your event…</span>
        ) : (
          <>
            <span>{locked ? "Continue to Timeline" : "Create Event & Continue to Timeline"}</span>
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
        {active && <span className="w-2 h-2 rounded-full bg-[var(--accent)]" />}
      </span>
      <span className="text-[11px] text-[var(--ink-muted)] leading-relaxed">{body}</span>
    </button>
  );
}

// =============================================================================================
// step 2 — review timeline

function TimelineStep({
  stages,
  warnings,
  saving,
  error,
  reparsing,
  reparseError,
  onReparse,
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
  reparsing: boolean;
  reparseError: string | null;
  onReparse: (p: ItineraryParsePayload) => void;
  onAddStage: () => void;
  onRemoveStage: (key: string) => void;
  onUpdateStage: (key: string, patch: Partial<DraftStage>) => void;
  onAddMoment: (key: string, label: string) => void;
  onRemoveMoment: (key: string, momentId: string) => void;
  startsOn: string | null;
  onBack: () => void;
  onNext: () => void;
}) {
  const [showReparse, setShowReparse] = useState(false);

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

      {/* Optional re-parse card */}
      <div className="glass-card p-4 rounded-2xl border border-white/10">
        <button
          type="button"
          onClick={() => setShowReparse((v) => !v)}
          className="w-full flex items-center justify-between text-xs font-semibold text-[var(--accent)] hover:underline"
        >
          <span className="flex items-center gap-1.5">
            <Sparkles className="w-3.5 h-3.5" />
            <span>{showReparse ? "Hide itinerary parser" : "Paste new itinerary to replace timeline"}</span>
          </span>
        </button>
        {showReparse && (
          <div className="mt-3 pt-3 border-t border-white/10">
            <ItineraryInputTabs onParse={onReparse} busy={reparsing} />
            {reparseError && <p className="text-xs text-[var(--danger)] mt-2">{reparseError}</p>}
          </div>
        )}
      </div>

      {stages.length === 0 ? (
        <div className="glass-card p-8 rounded-3xl border border-white/10 text-center">
          <p className="text-sm text-[var(--ivory)] mb-1">No stages yet</p>
          <p className="text-xs text-[var(--ink-muted)] mb-4">
            Add stages manually or use the itinerary parser above.
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
// step 3 — people & safety photos

function PeopleStep({
  eventId,
  suggestedPeople = [],
  onBack,
  onNext,
}: {
  eventId: string | null;
  suggestedPeople?: ParsedPerson[];
  onBack: () => void;
  onNext: () => void;
}) {
  const [equalFeatured, setEqualFeatured] = useState(false);
  const [people, setPeople] = useState<PersonDoc[]>([]);
  const [peopleError, setPeopleError] = useState<string | null>(null);
  const [selectedPrefill, setSelectedPrefill] = useState<{ name: string; tier?: number; role?: string } | null>(null);

  useEffect(() => {
    if (!eventId) return;
    return listenPeople(eventId, setPeople, () => setPeopleError("Couldn't load the people you've added."));
  }, [eventId]);

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
      {/* Equal Coverage Feature Toggle Card */}
      <div className={`glass-card p-5 rounded-3xl border transition-all duration-200 ${equalFeatured ? "border-[var(--accent)]/50 bg-[var(--accent)]/5" : "border-white/10"}`}>
        <label className="flex items-center justify-between gap-3 cursor-pointer">
          <span className="min-w-0">
            <span className="flex items-center gap-2 mb-1">
              <span className="text-xs font-semibold text-[var(--ivory)] uppercase tracking-wider">
                Equal Coverage Mode (All Inner Circle)
              </span>
              <span className={`text-[10px] font-mono font-bold px-2 py-0.5 rounded-full border ${equalFeatured ? "bg-emerald-500/15 text-emerald-400 border-emerald-500/30" : "bg-white/5 text-[var(--ink-muted)] border-white/10"}`}>
                {equalFeatured ? "Active: Default Tier 1" : "Default Tier 3"}
              </span>
            </span>
            <span className="block text-[11px] text-[var(--ink-muted)] leading-relaxed">
              When enabled, new additions default to <strong>Inner Circle</strong> for prominent screen coverage. When off, they default to <strong>Guest</strong>.
            </span>
          </span>
          <input
            type="checkbox"
            checked={equalFeatured}
            onChange={(e) => {
              const checked = e.target.checked;
              setEqualFeatured(checked);
              if (selectedPrefill) {
                setSelectedPrefill({ ...selectedPrefill, tier: checked ? 1 : 3 });
              }
            }}
            className="w-5 h-5 rounded accent-[var(--accent)] shrink-0 cursor-pointer"
          />
        </label>
      </div>

      {/* Suggested People Chips from AI Itinerary Parse */}
      {suggestedPeople.length > 0 && (
        <div className="glass-card p-5 rounded-3xl border border-[var(--accent)]/30 space-y-2.5 shadow-lg bg-gradient-to-b from-[var(--accent)]/5 to-transparent">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-1.5 text-xs font-semibold text-[var(--accent)]">
              <Sparkles className="w-3.5 h-3.5" />
              <span>AI Identified People from Itinerary — click to auto-fill:</span>
            </div>
            <span className="text-[10px] font-mono text-[var(--ink-muted)]">
              {suggestedPeople.length} detected
            </span>
          </div>

          <div className="flex flex-wrap gap-2 pt-1">
            {suggestedPeople.map((p, i) => {
              const alreadyAdded = people.some(
                (added) => (added.displayName || "").toLowerCase() === p.name.toLowerCase()
              );
              const isSelected = selectedPrefill?.name.toLowerCase() === p.name.toLowerCase();

              return (
                <button
                  key={i}
                  type="button"
                  onClick={() => {
                    setSelectedPrefill({
                      name: p.name,
                      tier: equalFeatured ? 1 : (p.tier ?? 1),
                      role: p.role,
                    });
                  }}
                  className={`text-xs px-3.5 py-1.5 rounded-full border flex items-center gap-1.5 transition-all duration-200 cursor-pointer active:scale-95 ${
                    isSelected
                      ? "bg-[var(--accent)] text-slate-950 font-bold border-transparent shadow-[0_0_16px_var(--accent-glow)] ring-2 ring-[var(--accent)]/60"
                      : alreadyAdded
                      ? "bg-emerald-500/15 border-emerald-500/30 text-emerald-300 opacity-75 hover:opacity-100"
                      : "bg-white/5 border-white/10 hover:border-[var(--accent)] text-[var(--ivory)] hover:bg-white/10"
                  }`}
                >
                  <UserPlus className="w-3 h-3" />
                  <span>{p.name}</span>
                  {p.role && (
                    <span className={`text-[10px] ${isSelected ? "text-slate-800" : "text-[var(--ink-muted)]"}`}>
                      ({p.role})
                    </span>
                  )}
                  {alreadyAdded && <span className="text-[10px] font-semibold">✓</span>}
                </button>
              );
            })}
          </div>
        </div>
      )}

      {/* Person Enrollment Form with Prefill Support */}
      <PersonEnrollForm
        eventId={eventId}
        defaultTier={equalFeatured ? 1 : 3}
        prefill={selectedPrefill}
        onAdded={() => {
          setSelectedPrefill(null);
        }}
      />

      {peopleError && <p className="text-xs text-[var(--danger)]">{peopleError}</p>}

      {/* Roster of Enrolled People */}
      {people.length > 0 && (
        <div className="glass-card p-5 rounded-3xl border border-white/10 shadow-lg">
          <p className="text-xs font-semibold text-[var(--ivory)] uppercase tracking-wider mb-3 flex items-center gap-2">
            <span>Enrolled Roster</span>
            <span className="text-[11px] font-mono px-2 py-0.5 rounded-full bg-white/10 text-[var(--accent)] font-bold">
              {people.length}
            </span>
          </p>
          <div className="flex flex-wrap gap-2">
            {people.map((p) => (
              <span
                key={p.personId}
                className="text-xs px-3 py-1.5 rounded-full bg-white/5 border border-white/10 text-[var(--ivory)] flex items-center gap-1.5 shadow-sm"
              >
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-400" />
                <span>{p.displayName || "Unnamed"}</span>
              </span>
            ))}
          </div>
        </div>
      )}

      <StepNav onBack={onBack} onNext={onNext} nextLabel={people.length > 0 ? "Continue to Launch" : "Skip to Launch"} />
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
        className="btn-secondary px-5 py-3 text-xs font-semibold flex items-center gap-1.5 rounded-full cursor-pointer active:scale-95"
      >
        <ArrowLeft className="w-3.5 h-3.5" />
        <span>Back</span>
      </button>
      <button
        type="button"
        onClick={onNext}
        className="btn-primary px-6 py-3 rounded-full text-xs font-semibold flex items-center gap-1.5 cursor-pointer active:scale-95 shadow-lg"
      >
        <span>{nextLabel}</span>
        <ArrowRight className="w-3.5 h-3.5" />
      </button>
    </div>
  );
}

// =============================================================================================
// step 4 — launch & links

function LaunchStep({ created, eventId }: { created: CreateEventResponse; eventId: string }) {
  const [copiedCode, setCopiedCode] = useState(false);
  const [copiedLink, setCopiedLink] = useState(false);
  const [copiedJoin, setCopiedJoin] = useState(false);

  // Normalize hostLink to browser origin so it doesn't show http://localhost:3000 in deployed environments
  const hostLinkUrl =
    typeof window !== "undefined"
      ? (() => {
          try {
            const parsed = new URL(created.hostLink, window.location.origin);
            return `${window.location.origin}${parsed.pathname}${parsed.search}`;
          } catch {
            return created.hostLink;
          }
        })()
      : created.hostLink;

  const joinUrl =
    typeof window !== "undefined"
      ? `${window.location.origin}/join/${eventId}${created.joinCode ? `?joinCode=${created.joinCode}` : ""}`
      : `/join/${eventId}`;

  return (
    <div className="space-y-4 animate-fadeIn">
      <div className="text-center mb-2">
        <div className="w-14 h-14 rounded-full bg-[var(--gold-500)]/15 text-[var(--accent)] flex items-center justify-center mx-auto mb-3 border border-[var(--gold-500)]/30 shadow-lg">
          <ShieldCheck className="w-7 h-7 stroke-[2]" />
        </div>
        <h2 className="font-[family-name:var(--font-display)] text-2xl sm:text-3xl font-semibold text-gold-gradient mb-1">
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
        value={hostLinkUrl}
        copied={copiedLink}
        onCopy={() => {
          void navigator.clipboard.writeText(hostLinkUrl);
          setCopiedLink(true);
          setTimeout(() => setCopiedLink(false), 2000);
        }}
        note="Send this to co-hosts who need full director and approval permissions."
      />

      {/* Guest Access Card — Guaranteed No Overflow */}
      <div className="glass-card p-5 sm:p-6 rounded-3xl border border-white/10 space-y-4 shadow-xl">
        <div className="flex items-center justify-between">
          <div>
            <h3 className="text-xs font-semibold text-[var(--ivory)] uppercase tracking-wider">
              Guest Access
            </h3>
            <p className="text-[11px] text-[var(--ink-muted)] mt-0.5">
              Share the QR code or link with your guests to let them join.
            </p>
          </div>
          {created.joinCode && (
            <span className="font-mono text-xs px-2.5 py-1 rounded-lg bg-white/10 border border-white/15 text-[var(--accent)] font-bold">
              Code: {created.joinCode}
            </span>
          )}
        </div>

        <div className="flex flex-col sm:flex-row items-center sm:items-start gap-5 pt-2">
          <div className="shrink-0">
            <HostJoinQr url={joinUrl} />
          </div>
          <div className="flex-1 min-w-0 w-full space-y-3 text-center sm:text-left">
            <p className="text-xs font-mono text-[var(--ivory)] bg-black/50 border border-white/10 rounded-xl p-3 break-all leading-relaxed shadow-inner">
              {joinUrl}
            </p>
            <button
              type="button"
              onClick={() => {
                void navigator.clipboard.writeText(joinUrl);
                setCopiedJoin(true);
                setTimeout(() => setCopiedJoin(false), 2000);
              }}
              className="btn-secondary w-full sm:w-auto px-5 py-2.5 text-xs font-semibold shadow-md active:scale-95 transition-all cursor-pointer"
            >
              {copiedJoin ? "✓ Copied Guest Link!" : "Copy Guest Link"}
            </button>
          </div>
        </div>
      </div>

      <GoogleUpgradeCard />

      <a
        href={`/host/${eventId}`}
        className="btn-primary w-full py-4 rounded-full text-sm font-semibold flex items-center justify-center gap-2 shadow-2xl mt-4 cursor-pointer active:scale-95"
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
}: {
  icon: React.ElementType;
  title: string;
  value: string;
  copied: boolean;
  onCopy: () => void;
  note?: string;
  mono?: boolean;
}) {
  return (
    <div className="glass-card p-5 rounded-3xl border border-white/10 flex flex-col gap-2 shadow-md">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Icon className="w-4 h-4 text-[var(--accent)]" />
          <span className="text-xs font-semibold text-[var(--ivory)] uppercase tracking-wider">
            {title}
          </span>
        </div>
        <button
          type="button"
          onClick={onCopy}
          className="text-xs font-semibold text-[var(--accent)] hover:underline cursor-pointer active:scale-95"
        >
          {copied ? "✓ Copied!" : "Copy"}
        </button>
      </div>
      <p
        className={`text-xs px-3.5 py-2.5 rounded-xl bg-black/50 border border-white/10 text-[var(--ivory)] break-all leading-relaxed shadow-inner ${
          mono ? "font-mono font-semibold text-[var(--accent)]" : ""
        }`}
      >
        {value}
      </p>
      {note && <p className="text-[11px] text-[var(--ink-muted)]">{note}</p>}
    </div>
  );
}
