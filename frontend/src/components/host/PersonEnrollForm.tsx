"use client";

import { useEffect, useRef, useState } from "react";
import { UserPlus, Upload, ShieldCheck, X, Crown, Gem, Sparkles, User, Check } from "lucide-react";
import { hostEnrollPerson } from "@/lib/hostApi";
import { ApiError } from "@/lib/api";

const MAX_PHOTO_BYTES = 6 * 1024 * 1024;

const TIER_CARDS: Array<{
  value: number;
  label: string;
  role: string;
  icon: React.ElementType;
}> = [
  { value: 0, label: "Principal", role: "Host, Bride, Groom", icon: Crown },
  { value: 1, label: "Inner Circle", role: "Family & Close Friends", icon: Gem },
  { value: 2, label: "Named VIP", role: "VIPs & Performers", icon: Sparkles },
  { value: 3, label: "Guest", role: "General Attendees", icon: User },
];

function friendlyEnrollError(err: unknown): string {
  if (err instanceof ApiError) {
    switch (err.code) {
      case "CONSENT_REQUIRED":
        return "Confirm you have this person's permission before adding their photo.";
      case "NO_FACE_DETECTED":
        return "Couldn't find a clear face in that photo — try a front-facing portrait.";
      case "BAD_SELFIE":
        return "That photo couldn't be read — try a different image.";
      case "SELFIE_TOO_LARGE":
        return "That photo is larger than 6MB — try a smaller image.";
      case "FACE_SERVICE_UNAVAILABLE":
        return "The face-recognition service is temporarily unavailable — try again shortly.";
      default:
        if (err.status === 429) return "Too many people added recently — wait a bit and try again.";
        return err.message || "Something went wrong adding that person.";
    }
  }
  return "Something went wrong adding that person.";
}

export function PersonEnrollForm({
  eventId,
  defaultTier = 3,
  prefill = null,
  onAdded,
}: {
  eventId: string;
  defaultTier?: number;
  prefill?: { name: string; tier?: number; role?: string } | null;
  onAdded?: (person: { personId: string; displayName: string; tier: number }) => void;
}) {
  const [name, setName] = useState(prefill?.name ?? "");
  const [tier, setTier] = useState<number>(prefill?.tier ?? defaultTier);
  const [photoBase64, setPhotoBase64] = useState<string | null>(null);
  const [photoPreview, setPhotoPreview] = useState<string | null>(null);
  const [photoName, setPhotoName] = useState<string | null>(null);
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [flash, setFlash] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Sync when prefill is selected (e.g. host clicked an itinerary-parsed person)
  useEffect(() => {
    if (prefill?.name) {
      setName(prefill.name);
      setTier(typeof prefill.tier === "number" ? prefill.tier : defaultTier);
      setError(null);
      setFlash(null);
    }
  }, [prefill, defaultTier]);

  // Sync defaultTier when no active custom prefill
  useEffect(() => {
    if (!prefill) {
      setTier(defaultTier);
    }
  }, [defaultTier, prefill]);

  function handlePhotoChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    setError(null);
    setFlash(null);
    if (!file) return;
    if (!["image/jpeg", "image/png"].includes(file.type)) {
      setError("Please choose a JPEG or PNG photo.");
      return;
    }
    if (file.size > MAX_PHOTO_BYTES) {
      setError("That photo is larger than 6MB — try a smaller one.");
      return;
    }
    setPhotoName(file.name);
    const reader = new FileReader();
    reader.onload = () => {
      const result = reader.result as string;
      const commaIdx = result.indexOf(",");
      setPhotoBase64(commaIdx >= 0 ? result.slice(commaIdx + 1) : result);
      setPhotoPreview(result);
    };
    reader.onerror = () => setError("Couldn't read that photo — try again.");
    reader.readAsDataURL(file);
  }

  function clearPhoto() {
    setPhotoBase64(null);
    setPhotoPreview(null);
    setPhotoName(null);
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  function reset() {
    setName("");
    setTier(defaultTier);
    clearPhoto();
    setConsent(false);
  }

  async function submit() {
    if (!name.trim() || !photoBase64 || !consent || busy) return;
    setBusy(true);
    setError(null);
    setFlash(null);
    try {
      const res = await hostEnrollPerson(eventId, {
        photo: photoBase64,
        displayName: name.trim(),
        tier,
        photoConsent: true,
      });
      setFlash(`✓ ${res.displayName} enrolled successfully.`);
      onAdded?.(res);
      reset();
    } catch (err) {
      setError(friendlyEnrollError(err));
    } finally {
      setBusy(false);
    }
  }

  const canSubmit = Boolean(name.trim() && photoBase64 && consent && !busy);

  return (
    <div className="p-5 sm:p-6 rounded-3xl glass-card border border-white/10 space-y-5 shadow-xl">
      <div className="flex items-center justify-between border-b border-white/10 pb-3">
        <div className="flex items-center gap-2">
          <span className="p-1.5 rounded-lg bg-[var(--accent)]/15 text-[var(--accent)] border border-[var(--accent)]/30">
            <UserPlus className="w-4 h-4" />
          </span>
          <span className="text-xs font-semibold text-[var(--ivory)] uppercase tracking-wider">
            Add Person Reference Photo
          </span>
        </div>

        {prefill?.name && (
          <span className="text-[10px] font-mono px-2 py-0.5 rounded-full bg-[var(--accent)]/15 border border-[var(--accent)]/30 text-[var(--accent)] font-semibold">
            Prefilled: {prefill.name}
          </span>
        )}
      </div>

      {/* Name Input */}
      <div>
        <label className="block text-[11px] font-semibold text-[var(--ink-muted)] uppercase tracking-wider mb-1.5">
          Full Name / Display Name
        </label>
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g. Rahul Sharma"
          className="w-full px-4 py-3 rounded-xl bg-black/50 border border-white/10 text-sm text-[var(--ivory)] placeholder:text-[var(--ink-faint)] focus:border-[var(--accent)] focus:outline-none transition-colors shadow-inner"
        />
      </div>

      {/* Interactive Visual Segmented Tier Selector */}
      <div>
        <div className="flex items-center justify-between mb-2">
          <label className="block text-[11px] font-semibold text-[var(--ink-muted)] uppercase tracking-wider">
            Coverage &amp; Priority Tier
          </label>
          <span className="text-[10px] text-[var(--ink-faint)] font-mono">
            {TIER_CARDS.find((t) => t.value === tier)?.role}
          </span>
        </div>

        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
          {TIER_CARDS.map((t) => {
            const isSelected = tier === t.value;
            const Icon = t.icon;
            return (
              <button
                key={t.value}
                type="button"
                onClick={() => setTier(t.value)}
                className={`p-3 rounded-2xl border text-left transition-all duration-200 cursor-pointer active:scale-95 flex flex-col justify-between min-h-[76px] ${
                  isSelected
                    ? "bg-[var(--accent)]/15 border-[var(--accent)]/60 text-white shadow-[0_0_20px_-4px_var(--accent-glow)] ring-1 ring-[var(--accent)]/40"
                    : "bg-white/[0.03] border-white/10 text-[var(--text-secondary)] hover:border-white/20 hover:text-white"
                }`}
              >
                <div className="flex items-center justify-between">
                  <Icon
                    className={`w-4 h-4 ${
                      isSelected ? "text-[var(--accent)]" : "text-[var(--ink-muted)]"
                    }`}
                  />
                  {isSelected && (
                    <span className="w-4 h-4 rounded-full bg-[var(--accent)] text-slate-950 flex items-center justify-center">
                      <Check className="w-2.5 h-2.5 stroke-[3]" />
                    </span>
                  )}
                </div>
                <div>
                  <p className={`text-xs font-semibold ${isSelected ? "text-[var(--ivory)] font-bold" : ""}`}>
                    {t.label}
                  </p>
                  <p className="text-[9px] text-[var(--ink-muted)] truncate leading-tight mt-0.5">
                    {t.role}
                  </p>
                </div>
              </button>
            );
          })}
        </div>
      </div>

      {/* Reference Photo Upload Dropzone */}
      <div>
        <label className="block text-[11px] font-semibold text-[var(--ink-muted)] uppercase tracking-wider mb-1.5">
          Reference Face Photo
        </label>
        {photoPreview ? (
          <div className="flex items-center gap-3.5 p-3.5 rounded-2xl bg-black/50 border border-white/15 shadow-inner">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={photoPreview}
              alt="Selected reference"
              className="w-14 h-14 rounded-xl object-cover border border-[var(--accent)]/40 shadow-md shrink-0"
            />
            <div className="flex-1 min-w-0">
              <p className="text-xs font-medium text-[var(--ivory)] truncate">
                {photoName || "Face Photo Loaded"}
              </p>
              <p className="text-[11px] text-emerald-400 font-mono mt-0.5 flex items-center gap-1">
                <Check className="w-3 h-3 stroke-[3]" />
                <span>Ready for face embedding</span>
              </p>
            </div>
            <button
              type="button"
              onClick={clearPhoto}
              aria-label="Remove photo"
              className="shrink-0 p-2 rounded-xl bg-white/5 hover:bg-rose-500/20 text-[var(--ink-muted)] hover:text-rose-400 border border-white/10 transition-colors"
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        ) : (
          <label className="flex flex-col items-center justify-center gap-2.5 p-6 rounded-2xl border border-dashed border-white/20 hover:border-[var(--accent)]/60 bg-white/[0.02] hover:bg-white/[0.04] text-center cursor-pointer transition-all duration-200">
            <div className="w-10 h-10 rounded-full bg-white/5 border border-white/10 flex items-center justify-center text-[var(--accent)]">
              <Upload className="w-5 h-5" />
            </div>
            <div>
              <p className="text-xs font-semibold text-[var(--ivory)] mb-0.5">
                Upload clear front-facing portrait
              </p>
              <p className="text-[11px] text-[var(--ink-muted)]">
                JPEG or PNG, up to 6MB — indexed securely inside event
              </p>
            </div>
            <input
              ref={fileInputRef}
              type="file"
              accept="image/jpeg,image/png"
              onChange={handlePhotoChange}
              className="hidden"
            />
          </label>
        )}
      </div>

      {/* Permission / Consent Styled Toggle Card */}
      <button
        type="button"
        onClick={() => setConsent(!consent)}
        className={`w-full p-3.5 rounded-2xl border text-left transition-all duration-200 flex items-start gap-3 cursor-pointer ${
          consent
            ? "bg-emerald-500/10 border-emerald-500/30 text-emerald-300"
            : "bg-white/[0.02] border-white/10 text-[var(--ink-muted)] hover:border-white/20"
        }`}
      >
        <div
          className={`w-5 h-5 rounded-lg border mt-0.5 flex items-center justify-center shrink-0 transition-colors ${
            consent
              ? "bg-emerald-500 border-emerald-400 text-slate-950"
              : "border-white/20 bg-black/40"
          }`}
        >
          {consent && <Check className="w-3.5 h-3.5 stroke-[3]" />}
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-xs font-medium text-[var(--ivory)] leading-relaxed">
            I confirm I have this person&rsquo;s permission to index their reference photo for this event.
          </p>
        </div>
      </button>

      {error && (
        <p className="text-xs text-[var(--danger)] p-3.5 rounded-xl bg-[var(--danger)]/15 border border-[var(--danger)]/30 font-medium">
          {error}
        </p>
      )}
      {flash && !error && (
        <p className="text-xs text-emerald-400 p-3 rounded-xl bg-emerald-500/10 border border-emerald-500/20 font-medium">
          {flash}
        </p>
      )}

      {/* Submit Button */}
      <button
        type="button"
        disabled={!canSubmit}
        onClick={() => void submit()}
        className="btn-primary w-full py-3.5 rounded-full text-xs font-semibold flex items-center justify-center gap-2 disabled:opacity-40 shadow-lg cursor-pointer active:scale-95"
      >
        <UserPlus className="w-4 h-4" />
        <span>{busy ? "Indexing Face & Adding…" : `Enroll ${name.trim() || "Person"}`}</span>
      </button>

      <div className="flex items-start gap-2 text-[11px] text-[var(--ink-faint)] leading-relaxed pt-2 border-t border-white/5">
        <ShieldCheck className="w-3.5 h-3.5 mt-0.5 shrink-0 text-emerald-400" />
        <span>
          Enrolling someone helps Showrunner track who&rsquo;s been photographed and surface missing shots. Guests claim their album with their own selfie and host approval.
        </span>
      </div>
    </div>
  );
}

