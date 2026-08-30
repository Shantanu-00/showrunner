"use client";

import { useRef, useState } from "react";
import { FileText, Image as ImageIcon, Sparkles, Type, Upload, X } from "lucide-react";
import type { ItineraryFileMime } from "@/lib/hostTypes";

const PDF_MAX_BYTES = 10 * 1024 * 1024;
const IMAGE_MAX_BYTES = 8 * 1024 * 1024;
const IMAGE_MIMES: ItineraryFileMime[] = ["image/jpeg", "image/png", "image/webp"];
const RAW_TEXT_MAX = 8000;

type Tab = "paste" | "pdf" | "screenshot";

/** Reads a `File` into base64, stripped of the `data:…;base64,` prefix — the shape
 * `POST …/itinerary/parse`'s `fileBase64` wants (`schemas/host.py::ParseItineraryRequest`). */
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

/** Paste / PDF / screenshot input for an itinerary — shared by the creation wizard's Step 2 and the
 * console's `ItineraryPanel`, so the three-input-modality contract (spec 13) has exactly one
 * implementation of file validation + base64 encoding rather than two that can drift apart.
 *
 * Deliberately does not call the API itself: the two callers need the response for different
 * reasons (populate the review step vs. replace the panel's draft stages), so this only ever hands
 * back a ready-to-send payload via `onParse`. */
export function ItineraryInputTabs({
  onParse,
  busy,
  disabled,
}: {
  onParse: (payload: ItineraryParsePayload) => void;
  busy: boolean;
  disabled?: boolean;
}) {
  const [tab, setTab] = useState<Tab>("paste");
  const [rawText, setRawText] = useState("");
  const [fileName, setFileName] = useState<string | null>(null);
  const [fileBase64, setFileBase64] = useState<string | null>(null);
  const [fileMime, setFileMime] = useState<ItineraryFileMime | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const pdfInput = useRef<HTMLInputElement>(null);
  const imageInput = useRef<HTMLInputElement>(null);

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
      setFileError(isPdf ? "That doesn't look like a PDF." : "Screenshots must be JPEG, PNG or WebP.");
      return;
    }
    const limit = isPdf ? PDF_MAX_BYTES : IMAGE_MAX_BYTES;
    if (file.size > limit) {
      setFileError(`That file is over ${limit / (1024 * 1024)} MB — try a smaller export or a crop.`);
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
    setTab(next);
    setFileError(null);
  }

  const canParse = !disabled && !busy && (rawText.trim().length > 0 || fileBase64 !== null);

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-1.5 p-1 rounded-xl bg-black/40 border border-white/10 w-fit">
        <TabButton icon={Type} label="Paste text" active={tab === "paste"} onClick={() => switchTab("paste")} />
        <TabButton icon={FileText} label="PDF" active={tab === "pdf"} onClick={() => switchTab("pdf")} />
        <TabButton
          icon={ImageIcon}
          label="Screenshot"
          active={tab === "screenshot"}
          onClick={() => switchTab("screenshot")}
        />
      </div>

      {tab === "paste" && (
        <div>
          <textarea
            value={rawText}
            onChange={(e) => setRawText(e.target.value.slice(0, RAW_TEXT_MAX))}
            disabled={disabled}
            placeholder="Paste the itinerary — a WhatsApp forward, an invitation timeline, a run-of-show…"
            rows={5}
            maxLength={RAW_TEXT_MAX}
            className="w-full px-4 py-3 rounded-xl bg-black/50 border border-white/10 text-xs text-[var(--ivory)] placeholder:text-[var(--ink-faint)] focus:border-[var(--accent)] focus:outline-none disabled:opacity-50"
          />
          <p className="text-[11px] text-[var(--ink-faint)] mt-1 text-right tabular-nums">
            {rawText.length} / {RAW_TEXT_MAX}
          </p>
        </div>
      )}

      {(tab === "pdf" || tab === "screenshot") && (
        <div>
          {fileName ? (
            <div className="flex items-center gap-2 p-3 rounded-xl bg-black/40 border border-white/10">
              {tab === "pdf" ? (
                <FileText className="w-4 h-4 text-[var(--accent)] shrink-0" />
              ) : (
                <ImageIcon className="w-4 h-4 text-[var(--accent)] shrink-0" />
              )}
              <span className="flex-1 min-w-0 text-xs text-[var(--ivory)] truncate">{fileName}</span>
              <button
                type="button"
                onClick={clearFile}
                aria-label="Remove file"
                className="shrink-0 p-1 rounded-full hover:bg-white/10 text-[var(--ink-muted)] hover:text-[var(--danger)]"
              >
                <X className="w-3.5 h-3.5" />
              </button>
            </div>
          ) : (
            <label
              className={`flex flex-col items-center justify-center gap-2 p-6 rounded-xl border border-dashed border-white/15 text-center cursor-pointer hover:border-[var(--accent)]/50 transition-colors ${
                disabled ? "opacity-50 pointer-events-none" : ""
              }`}
            >
              <Upload className="w-5 h-5 text-[var(--ink-muted)]" />
              <span className="text-xs text-[var(--ink-muted)]">
                {tab === "pdf" ? "Upload a PDF (up to 10 MB)" : "Upload a screenshot (JPEG/PNG/WebP, up to 8 MB)"}
              </span>
              <input
                ref={tab === "pdf" ? pdfInput : imageInput}
                type="file"
                accept={tab === "pdf" ? "application/pdf" : "image/jpeg,image/png,image/webp"}
                disabled={disabled}
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  if (file) void handleFile(file, tab);
                }}
                className="hidden"
              />
            </label>
          )}
          {fileError && <p className="text-xs text-[var(--danger)] mt-2">{fileError}</p>}
        </div>
      )}

      <button
        type="button"
        disabled={!canParse}
        onClick={() =>
          onParse({
            rawText: rawText.trim() || undefined,
            fileBase64: fileBase64 ?? undefined,
            fileMime: fileMime ?? undefined,
          })
        }
        className="btn-secondary px-4 py-2 text-xs font-semibold flex items-center gap-1.5 disabled:opacity-40"
      >
        <Sparkles className="w-3.5 h-3.5 text-[var(--accent)]" />
        <span>{busy ? "Extracting timeline…" : "Parse itinerary"}</span>
      </button>
    </div>
  );
}

function TabButton({
  icon: Icon,
  label,
  active,
  onClick,
}: {
  icon: React.ElementType;
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors ${
        active ? "bg-[var(--accent)] text-black" : "text-[var(--ink-muted)] hover:text-[var(--ivory)]"
      }`}
    >
      <Icon className="w-3.5 h-3.5" />
      <span>{label}</span>
    </button>
  );
}
