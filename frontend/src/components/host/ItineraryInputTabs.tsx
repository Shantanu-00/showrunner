"use client";

import { useEffect, useRef, useState } from "react";
import { FileText, Image as ImageIcon, Sparkles, Type, Upload, X, Loader2, CheckCircle2 } from "lucide-react";
import type { ItineraryFileMime } from "@/lib/hostTypes";

const PDF_MAX_BYTES = 10 * 1024 * 1024;
const IMAGE_MAX_BYTES = 8 * 1024 * 1024;
const IMAGE_MIMES: ItineraryFileMime[] = ["image/jpeg", "image/png", "image/webp"];
const RAW_TEXT_MAX = 8000;

type Tab = "paste" | "pdf" | "screenshot";

const PARSE_STEPS = [
  "Reading itinerary document & notes…",
  "Extracting timeline, stages & multi-day dates…",
  "Identifying key people, roles & moments…",
  "Synthesizing director schedule with Gemini 3.7 Flash…",
];

function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = String(reader.result ?? "");
      const comma = result.indexOf(",");
      resolve(comma >= 0 ? result.slice(comma + 1) : result);
    };
    reader.onerror = () => reject(reader.error ?? new Error("couldn't read that file"));
    reader.readAsDataURL(file);
  });
}

export interface ItineraryParsePayload {
  rawText?: string;
  fileBase64?: string;
  fileMime?: ItineraryFileMime;
}

export function ItineraryInputTabs({
  onParse,
  busy,
  disabled,
  buttonLabel = "Extract Event with Gemini 3.7 Flash",
  placeholder = "Paste the itinerary — a WhatsApp forward, invitation schedule, trip notes, wedding run-of-show…",
}: {
  onParse: (payload: ItineraryParsePayload) => void;
  busy: boolean;
  disabled?: boolean;
  buttonLabel?: string;
  placeholder?: string;
}) {
  const [tab, setTab] = useState<Tab>("paste");
  const [rawText, setRawText] = useState("");
  const [fileName, setFileName] = useState<string | null>(null);
  const [fileBase64, setFileBase64] = useState<string | null>(null);
  const [fileMime, setFileMime] = useState<ItineraryFileMime | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [stepIdx, setStepIdx] = useState(0);
  const pdfInput = useRef<HTMLInputElement>(null);
  const imageInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!busy) {
      setStepIdx(0);
      return;
    }
    const interval = setInterval(() => {
      setStepIdx((prev) => (prev < PARSE_STEPS.length - 1 ? prev + 1 : prev));
    }, 1800);
    return () => clearInterval(interval);
  }, [busy]);

  function clearFile() {
    setFileName(null);
    setFileBase64(null);
    setFileMime(null);
    setFileError(null);
    if (pdfInput.current) pdfInput.current.value = "";
    if (imageInput.current) imageInput.current.value = "";
  }

  async function handleFile(file: File, kind: "pdf" | "screenshot") {
    setFileError(null);
    const isPdf = kind === "pdf";
    const okMime = isPdf ? file.type === "application/pdf" : IMAGE_MIMES.includes(file.type as ItineraryFileMime);
    if (!okMime) {
      setFileError(isPdf ? "Please choose a valid PDF document." : "Screenshots must be JPEG, PNG or WebP.");
      return;
    }
    const limit = isPdf ? PDF_MAX_BYTES : IMAGE_MAX_BYTES;
    if (file.size > limit) {
      setFileError(`That file is over ${limit / (1024 * 1024)} MB — try a smaller file.`);
      return;
    }
    try {
      const b64 = await fileToBase64(file);
      setFileBase64(b64);
      setFileMime(file.type as ItineraryFileMime);
      setFileName(file.name);
    } catch {
      setFileError("Couldn't read that file — try again or paste the text instead.");
    }
  }

  function switchTab(next: Tab) {
    if (busy) return;
    setTab(next);
    setFileError(null);
  }

  const canParse = !disabled && !busy && (rawText.trim().length > 0 || fileBase64 !== null);

  return (
    <div className="space-y-4">
      {/* Tab Segment Controls */}
      <div className="flex items-center gap-1.5 p-1 rounded-2xl bg-black/50 border border-white/10 w-fit shadow-inner">
        <TabButton icon={Type} label="Paste text" active={tab === "paste"} disabled={busy} onClick={() => switchTab("paste")} />
        <TabButton icon={FileText} label="PDF Document" active={tab === "pdf"} disabled={busy} onClick={() => switchTab("pdf")} />
        <TabButton
          icon={ImageIcon}
          label="Screenshot / Image"
          active={tab === "screenshot"}
          disabled={busy}
          onClick={() => switchTab("screenshot")}
        />
      </div>

      {/* Paste Text Tab */}
      {tab === "paste" && (
        <div>
          <textarea
            value={rawText}
            onChange={(e) => setRawText(e.target.value.slice(0, RAW_TEXT_MAX))}
            disabled={disabled || busy}
            placeholder={placeholder}
            rows={5}
            maxLength={RAW_TEXT_MAX}
            className="w-full px-4 py-3.5 rounded-2xl bg-black/50 border border-white/10 text-xs text-[var(--ivory)] placeholder:text-[var(--ink-faint)] focus:border-[var(--accent)] focus:outline-none transition-colors shadow-inner disabled:opacity-50 leading-relaxed font-sans"
          />
          <div className="flex items-center justify-between text-[11px] text-[var(--ink-faint)] mt-1 px-1">
            <span>Tip: Paste unformatted WhatsApp messages, agendas, or trip notes</span>
            <span className="tabular-nums font-mono">{rawText.length} / {RAW_TEXT_MAX}</span>
          </div>
        </div>
      )}

      {/* PDF / Image Upload Tab */}
      {(tab === "pdf" || tab === "screenshot") && (
        <div>
          {fileName ? (
            <div className="flex items-center gap-3 p-3.5 rounded-2xl bg-black/50 border border-white/15 shadow-inner">
              <div className="p-2 rounded-xl bg-[var(--accent)]/15 text-[var(--accent)] border border-[var(--accent)]/30 shrink-0">
                {tab === "pdf" ? <FileText className="w-5 h-5" /> : <ImageIcon className="w-5 h-5" />}
              </div>
              <div className="flex-1 min-w-0">
                <span className="text-xs font-medium text-[var(--ivory)] truncate block">{fileName}</span>
                <span className="text-[10px] text-emerald-400 font-mono flex items-center gap-1 mt-0.5">
                  <CheckCircle2 className="w-3 h-3" />
                  <span>Ready for Gemini analysis</span>
                </span>
              </div>
              {!busy && (
                <button
                  type="button"
                  onClick={clearFile}
                  aria-label="Remove file"
                  className="shrink-0 p-2 rounded-xl bg-white/5 hover:bg-rose-500/20 text-[var(--ink-muted)] hover:text-rose-400 border border-white/10 transition-colors"
                >
                  <X className="w-4 h-4" />
                </button>
              )}
            </div>
          ) : (
            <label
              className={`flex flex-col items-center justify-center gap-2.5 p-7 rounded-2xl border border-dashed border-white/20 hover:border-[var(--accent)]/60 bg-white/[0.02] hover:bg-white/[0.04] text-center cursor-pointer transition-all duration-200 ${
                disabled || busy ? "opacity-50 pointer-events-none" : ""
              }`}
            >
              <div className="w-10 h-10 rounded-full bg-white/5 border border-white/10 flex items-center justify-center text-[var(--accent)] shadow-sm">
                <Upload className="w-5 h-5" />
              </div>
              <div>
                <p className="text-xs font-semibold text-[var(--ivory)] mb-0.5">
                  {tab === "pdf" ? "Upload Itinerary PDF (up to 10 MB)" : "Upload Schedule Screenshot (JPEG/PNG/WebP, up to 8 MB)"}
                </p>
                <p className="text-[11px] text-[var(--ink-muted)]">
                  Click or drag and drop your document here
                </p>
              </div>
              <input
                ref={tab === "pdf" ? pdfInput : imageInput}
                type="file"
                accept={tab === "pdf" ? "application/pdf" : "image/jpeg,image/png,image/webp"}
                disabled={disabled || busy}
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  if (file) void handleFile(file, tab);
                }}
                className="hidden"
              />
            </label>
          )}
          {fileError && (
            <p className="text-xs text-[var(--danger)] mt-2 p-2.5 rounded-xl bg-[var(--danger)]/10 border border-[var(--danger)]/20">
              {fileError}
            </p>
          )}
        </div>
      )}

      {/* Real-time AI Loading Progress State */}
      {busy && (
        <div className="p-4 rounded-2xl bg-[var(--accent)]/10 border border-[var(--accent)]/30 space-y-2.5 shadow-lg animate-spring-in">
          <div className="flex items-center gap-2.5 text-xs font-semibold text-[var(--accent)]">
            <Loader2 className="w-4 h-4 animate-spin text-[var(--accent)]" />
            <span className="font-mono tracking-wide uppercase text-[11px]">Gemini 3.7 Flash Directing</span>
          </div>
          <p className="text-xs text-[var(--ivory)] font-medium transition-all duration-300">
            {PARSE_STEPS[stepIdx]}
          </p>
          <div className="w-full bg-black/40 rounded-full h-1.5 overflow-hidden border border-white/10">
            <div
              className="bg-gradient-to-r from-[var(--accent)] to-[var(--accent-soft)] h-full transition-all duration-500 rounded-full"
              style={{ width: `${Math.min((stepIdx + 1) * 25, 95)}%` }}
            />
          </div>
        </div>
      )}

      {/* Parse Action Button */}
      <button
        type="button"
        disabled={!canParse || busy}
        onClick={() => {
          if (!canParse || busy) return;
          onParse({
            rawText: rawText.trim() || undefined,
            fileBase64: fileBase64 ?? undefined,
            fileMime: fileMime ?? undefined,
          });
        }}
        className="btn-primary px-6 py-3 rounded-full text-xs font-semibold flex items-center justify-center gap-2 shadow-xl disabled:opacity-40 w-full sm:w-auto cursor-pointer active:scale-95 transition-all"
      >
        {busy ? (
          <>
            <Loader2 className="w-4 h-4 animate-spin" />
            <span>Analyzing Itinerary with Gemini…</span>
          </>
        ) : (
          <>
            <Sparkles className="w-4 h-4" />
            <span>{buttonLabel}</span>
          </>
        )}
      </button>
    </div>
  );
}

function TabButton({
  icon: Icon,
  label,
  active,
  disabled,
  onClick,
}: {
  icon: React.ElementType;
  label: string;
  active: boolean;
  disabled?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className={`flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-medium transition-all duration-200 cursor-pointer active:scale-95 disabled:opacity-50 ${
        active
          ? "bg-[var(--accent)] text-slate-950 font-bold shadow-md"
          : "text-[var(--text-secondary)] hover:text-white hover:bg-white/5"
      }`}
    >
      <Icon className="w-3.5 h-3.5" />
      <span>{label}</span>
    </button>
  );
}

