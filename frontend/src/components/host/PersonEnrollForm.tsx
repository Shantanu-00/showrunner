"use client";

import { useEffect, useRef, useState } from "react";
import { UserPlus, Upload, ShieldCheck, X } from "lucide-react";
import { hostEnrollPerson } from "@/lib/hostApi";
import { ApiError } from "@/lib/api";

// 6MB — the task brief's client-side ceiling. The server has its own SELFIE_TOO_LARGE gate on the
// decoded bytes; this just saves a host a round trip to discover an obviously-too-big file.
const MAX_PHOTO_BYTES = 6 * 1024 * 1024;

const TIER_OPTIONS: { value: number; label: string }[] = [
  { value: 0, label: "0 · Principal" },
  { value: 1, label: "1 · Inner circle" },
  { value: 2, label: "2 · Named VIP" },
  { value: 3, label: "3 · Guest" },
];

/** Friendly copy per `POST …/people/host-enroll` error code (spec 13 §7) — a host adding someone
 * from a phone in a noisy room should never see a raw error code or a stack-shaped message. */
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
        return "That photo is too large for the face service — try a smaller image.";
      case "FACE_SERVICE_UNAVAILABLE":
        return "The face-recognition service is temporarily unavailable — try again shortly.";
      default:
        if (err.status === 429) return "Too many people added recently — wait a bit and try again.";
        return err.message || "Something went wrong adding that person.";
    }
  }
  return "Something went wrong adding that person.";
}

/**
 * The host-enrollment form — shared verbatim by the People panel (`PeoplePanel.tsx`) and the
 * wizard's Step 4 (`HostWizard.tsx`), because both are the same action at a different point in
 * the host's session: name, tier, a reference photo, and an explicit, unchecked-by-default
 * permission acknowledgment. See `POST …/people/host-enroll` (spec 13 §7).
 *
 * `defaultTier` is a pure client-side default for *this form's next untouched add* — the wizard's
 * "everyone is equally featured" toggle flips it between 1 and 3, but a host who already picked a
 * tier for the person they're currently adding is never silently overridden.
 */
export function PersonEnrollForm({
  eventId,
  defaultTier = 3,
  onAdded,
}: {
  eventId: string;
  defaultTier?: number;
  onAdded?: (person: { personId: string; displayName: string; tier: number }) => void;
}) {
  const [name, setName] = useState("");
  const [tier, setTier] = useState(defaultTier);
  const [photoBase64, setPhotoBase64] = useState<string | null>(null);
  const [photoPreview, setPhotoPreview] = useState<string | null>(null);
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [flash, setFlash] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const tierTouchedRef = useRef(false);

  useEffect(() => {
    if (!tierTouchedRef.current) setTier(defaultTier);
  }, [defaultTier]);

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
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  function reset() {
    setName("");
    setTier(defaultTier);
    tierTouchedRef.current = false;
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
      setFlash(`${res.displayName} added.`);
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
    <div className="p-5 rounded-2xl bg-white/[0.03] border border-white/10 space-y-4">
      <div className="flex items-center gap-2 text-xs font-semibold text-[var(--ivory)] uppercase tracking-wider">
        <UserPlus className="w-4 h-4 text-[var(--accent)]" />
        <span>Add a person</span>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <div>
          <label className="block text-[11px] font-semibold text-[var(--ink-muted)] uppercase tracking-wider mb-1.5">
            Name
          </label>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. Priya Sharma"
            className="w-full px-3.5 py-2.5 rounded-xl bg-black/40 border border-white/10 text-sm text-[var(--ivory)] placeholder:text-[var(--ink-faint)] focus:border-[var(--accent)] focus:outline-none"
          />
        </div>
        <div>
          <label className="block text-[11px] font-semibold text-[var(--ink-muted)] uppercase tracking-wider mb-1.5">
            Tier
          </label>
          <select
            value={tier}
            onChange={(e) => {
              tierTouchedRef.current = true;
              setTier(Number(e.target.value));
            }}
            className="w-full px-3.5 py-2.5 rounded-xl bg-black/40 border border-white/10 text-sm text-[var(--ivory)] focus:border-[var(--accent)] focus:outline-none"
          >
            {TIER_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div>
        <label className="block text-[11px] font-semibold text-[var(--ink-muted)] uppercase tracking-wider mb-1.5">
          Reference photo
        </label>
        {photoPreview ? (
          <div className="flex items-center gap-3 p-3 rounded-xl bg-black/40 border border-white/10">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={photoPreview}
              alt="Selected reference"
              className="w-12 h-12 rounded-lg object-cover border border-white/10 shrink-0"
            />
            <span className="flex-1 min-w-0 text-xs text-[var(--ink-muted)]">Photo ready</span>
            <button
              type="button"
              onClick={clearPhoto}
              aria-label="Remove photo"
              className="shrink-0 p-1 rounded-full hover:bg-white/10 text-[var(--ink-muted)] hover:text-[var(--danger)]"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
        ) : (
          <label className="flex flex-col items-center justify-center gap-2 p-5 rounded-xl border border-dashed border-white/15 text-center cursor-pointer hover:border-[var(--accent)]/50 transition-colors">
            <Upload className="w-5 h-5 text-[var(--ink-muted)]" />
            <span className="text-xs text-[var(--ink-muted)]">One clear, front-facing photo — JPEG or PNG, up to 6MB</span>
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

      <label className="flex items-start gap-2.5 cursor-pointer">
        <input
          type="checkbox"
          checked={consent}
          onChange={(e) => setConsent(e.target.checked)}
          className="w-4 h-4 mt-0.5 rounded accent-[var(--accent)]"
        />
        <span className="text-xs text-[var(--ivory-dim)] leading-relaxed">
          I have this person&rsquo;s permission to add their photo.
        </span>
      </label>

      {error && (
        <p className="text-xs text-[var(--danger)] p-3 rounded-xl bg-[var(--danger)]/10 border border-[var(--danger)]/20">
          {error}
        </p>
      )}
      {flash && !error && <p className="text-xs text-[var(--ok)] font-medium">{flash}</p>}

      <button
        type="button"
        disabled={!canSubmit}
        onClick={() => void submit()}
        className="btn-primary w-full py-3 rounded-full text-xs font-semibold flex items-center justify-center gap-2 disabled:opacity-40"
      >
        <UserPlus className="w-4 h-4" />
        <span>{busy ? "Adding…" : "Add person"}</span>
      </button>

      <p className="flex items-start gap-1.5 text-[11px] text-[var(--ink-faint)] leading-relaxed pt-3 border-t border-white/5">
        <ShieldCheck className="w-3.5 h-3.5 mt-0.5 shrink-0" />
        <span>
          Adding someone helps Showrunner track who&rsquo;s been photographed. It never opens their
          album to anyone — they claim it themselves with a selfie, and you approve it.
        </span>
      </p>
    </div>
  );
}
