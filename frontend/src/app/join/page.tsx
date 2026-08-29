"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowRight, QrCode, Ticket, Camera, X, Info } from "lucide-react";
import { ensureAnonymousAuth } from "@/lib/firebase";
import { authedJson, ApiError } from "@/lib/api";

/** `POST /v1/events/join-code` — resolve an invite code to the event it belongs to.
 *
 * Called inline rather than through `lib/api.ts` because that file is another workstream's surface
 * this session; the shape and the `authedJson` transport are the file's own conventions. */
function resolveJoinCode(code: string): Promise<{ eventId: string; eventName?: string | null }> {
  return authedJson("/v1/events/join-code", { method: "POST", body: JSON.stringify({ code }) });
}

/** A ULID is 26 chars of Crockford base32 — a host who pastes the event id straight out of their
 * console URL should not be told their code is wrong. */
const ULID_RE = /^[0-9A-HJKMNP-TV-Z]{26}$/i;

function joinPath(eventId: string, code?: string | null): string {
  // The invite code rides on to `/join/{eventId}` rather than being consumed here: this page only
  // resolves *which* event a code belongs to, and the guest shell is what redeems it for membership.
  // Dropping it would land a guest on an invite-only event's door with the key left behind.
  return `/join/${encodeURIComponent(eventId)}${code ? `?joinCode=${encodeURIComponent(code)}` : ""}`;
}

/** Accepts anything a guest could plausibly paste: a full join URL (with or without its `joinCode`),
 * a bare event id, or an invite code. Returns a destination when the input already carries the event
 * id, otherwise null — in which case the code has to be resolved server-side. */
function destinationFromInput(raw: string): string | null {
  const trimmed = raw.trim();
  if (!trimmed) return null;
  if (trimmed.includes("/join/")) {
    const tail = trimmed.split("/join/")[1] ?? "";
    const seg = tail.split(/[/?#]/)[0];
    if (!seg) return null;
    const query = tail.includes("?") ? tail.slice(tail.indexOf("?") + 1) : "";
    const code = new URLSearchParams(query).get("joinCode");
    return joinPath(decodeURIComponent(seg), code);
  }
  if (ULID_RE.test(trimmed)) return joinPath(trimmed.toUpperCase());
  return null;
}

// A QR-code *reader* is not in the dependency list (the `qrcode` package only writes them), and the
// platform ships one: BarcodeDetector is available in Chrome on Android, which is the device this
// affordance exists for. Where it isn't, the fallback is honest and better anyway — every modern
// phone camera app decodes a QR code and opens the link directly.
type DetectedBarcode = { rawValue: string };
type BarcodeDetectorLike = { detect(source: HTMLVideoElement): Promise<DetectedBarcode[]> };
type BarcodeDetectorCtor = new (opts: { formats: string[] }) => BarcodeDetectorLike;

function barcodeDetectorCtor(): BarcodeDetectorCtor | null {
  if (typeof window === "undefined") return null;
  const ctor = (window as unknown as { BarcodeDetector?: BarcodeDetectorCtor }).BarcodeDetector;
  return typeof ctor === "function" ? ctor : null;
}

export default function JoinEntryPage() {
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [scanning, setScanning] = useState(false);
  const [scanSupported, setScanSupported] = useState(false);

  useEffect(() => {
    void ensureAnonymousAuth().catch(() => {});
    setScanSupported(barcodeDetectorCtor() !== null);
  }, []);

  const go = useCallback((path: string) => {
    // A full navigation, not a router push: `/join/{eventId}` is a different statically exported
    // document, and the guest shell reads the invite code off `window.location` on mount.
    window.location.assign(path);
  }, []);

  const submit = useCallback(
    async (raw: string) => {
      const value = raw.trim();
      if (!value) return;
      const direct = destinationFromInput(value);
      if (direct) {
        go(direct);
        return;
      }
      setBusy(true);
      setError(null);
      try {
        await ensureAnonymousAuth();
        const res = await resolveJoinCode(value);
        go(joinPath(res.eventId, value));
      } catch (err) {
        setError(
          err instanceof ApiError && err.status === 404
            ? "No event matches that code. Check it with whoever invited you — codes can also be turned off after an event ends."
            : "That code didn't work. Check it and try again."
        );
        setBusy(false);
      }
    },
    [go]
  );

  return (
    <main className="min-h-screen px-5 py-16 mx-auto max-w-md">
      <header className="mb-8 text-center">
        <div className="w-14 h-14 rounded-full bg-[var(--gold-500)]/15 text-[var(--accent)] border border-[var(--gold-500)]/30 flex items-center justify-center mx-auto mb-4">
          <Ticket className="w-7 h-7" />
        </div>
        <h1 className="font-[family-name:var(--font-display)] text-3xl font-semibold text-gold-gradient mb-2">
          Join an event
        </h1>
        <p className="text-xs text-[var(--ink-muted)] leading-relaxed max-w-xs mx-auto">
          Enter the invite code from your host. You&rsquo;ll be in straight away — no account, no
          email address.
        </p>
      </header>

      <div className="glass-card p-6 rounded-3xl border border-white/10 shadow-xl mb-4">
        <label
          htmlFor="join-code"
          className="block text-xs font-semibold text-[var(--ivory)] uppercase tracking-wider mb-2"
        >
          Invite code
        </label>
        <input
          id="join-code"
          value={code}
          onChange={(e) => {
            setCode(e.target.value);
            setError(null);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter") void submit(code);
          }}
          autoComplete="off"
          autoCapitalize="characters"
          spellCheck={false}
          placeholder="e.g. VELVET-1204"
          className="w-full px-4 py-3.5 rounded-xl bg-black/50 border border-white/10 text-base font-mono text-center tracking-[0.12em] text-[var(--ivory)] placeholder:text-[var(--ink-faint)] placeholder:tracking-normal focus:border-[var(--accent)] focus:outline-none transition-colors"
        />

        {error && (
          <p className="text-xs text-[var(--danger)] mt-3 leading-relaxed">{error}</p>
        )}

        <button
          type="button"
          disabled={busy || !code.trim()}
          onClick={() => void submit(code)}
          className="btn-primary w-full mt-4 py-3.5 rounded-full text-sm font-semibold flex items-center justify-center gap-2 disabled:opacity-40"
        >
          <span>{busy ? "Finding your event…" : "Join the event"}</span>
          {!busy && <ArrowRight className="w-4 h-4 stroke-[2.5]" />}
        </button>

        <p className="text-[11px] text-[var(--ink-faint)] mt-3 text-center leading-relaxed">
          A full link or an event id works here too.
        </p>
      </div>

      <div className="glass-card p-5 rounded-3xl border border-white/10">
        <div className="flex items-center gap-2 mb-2">
          <QrCode className="w-4 h-4 text-[var(--accent)]" />
          <h2 className="text-xs font-semibold text-[var(--ivory)] uppercase tracking-wider">
            Scan a QR code instead
          </h2>
        </div>
        {scanSupported ? (
          <>
            <p className="text-xs text-[var(--ink-muted)] leading-relaxed mb-3">
              There&rsquo;s usually one on the wall, on the tables, or on the big screen.
            </p>
            <button
              type="button"
              onClick={() => setScanning(true)}
              className="btn-secondary w-full py-3 rounded-full text-xs font-semibold flex items-center justify-center gap-2"
            >
              <Camera className="w-4 h-4" />
              <span>Open the scanner</span>
            </button>
          </>
        ) : (
          <p className="text-xs text-[var(--ink-muted)] leading-relaxed">
            Point your phone&rsquo;s own camera app at the QR code on the wall or the big screen — it
            opens the event directly, and you can skip this page entirely.
          </p>
        )}
      </div>

      <p className="mt-8 text-[11px] text-[var(--ink-faint)] text-center leading-relaxed">
        Hosting instead?{" "}
        <a href="/host" className="text-[var(--accent)] hover:underline font-semibold">
          Create your own event
        </a>
      </p>

      {scanning && (
        <QrScanner
          onClose={() => setScanning(false)}
          onResult={(value) => {
            setScanning(false);
            const direct = destinationFromInput(value);
            if (direct) {
              go(direct);
              return;
            }
            setCode(value);
            void submit(value);
          }}
        />
      )}
    </main>
  );
}

function QrScanner({
  onClose,
  onResult,
}: {
  onClose: () => void;
  onResult: (value: string) => void;
}) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [fault, setFault] = useState<string | null>(null);

  useEffect(() => {
    const Ctor = barcodeDetectorCtor();
    if (!Ctor) {
      setFault("This browser can't scan — type the code in instead.");
      return;
    }
    let stream: MediaStream | null = null;
    let raf = 0;
    let stopped = false;
    const detector = new Ctor({ formats: ["qr_code"] });

    async function tick() {
      const video = videoRef.current;
      if (stopped || !video || video.readyState < 2) {
        raf = requestAnimationFrame(() => void tick());
        return;
      }
      try {
        const found = await detector.detect(video);
        const hit = found.find((f) => f.rawValue);
        if (hit && !stopped) {
          onResult(hit.rawValue);
          return;
        }
      } catch {
        // A single failed frame is not a failed scan — keep looking.
      }
      raf = requestAnimationFrame(() => void tick());
    }

    navigator.mediaDevices
      ?.getUserMedia({ video: { facingMode: "environment" } })
      .then((s) => {
        if (stopped) {
          s.getTracks().forEach((t) => t.stop());
          return;
        }
        stream = s;
        if (videoRef.current) {
          videoRef.current.srcObject = s;
          void videoRef.current.play();
        }
        void tick();
      })
      .catch(() => {
        setFault("Camera access was blocked — type the code in instead.");
      });

    return () => {
      stopped = true;
      cancelAnimationFrame(raf);
      stream?.getTracks().forEach((t) => t.stop());
    };
  }, [onResult]);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Scan an event QR code"
      className="fixed inset-0 z-50 bg-black/90 flex flex-col items-center justify-center px-6"
    >
      <button
        type="button"
        onClick={onClose}
        aria-label="Close the scanner"
        className="absolute top-5 right-5 w-11 h-11 rounded-full bg-white/10 border border-white/15 text-[var(--ivory)] flex items-center justify-center"
      >
        <X className="w-5 h-5" />
      </button>

      <div className="relative w-full max-w-xs aspect-square rounded-3xl overflow-hidden border border-[var(--gold-500)]/40">
        <video
          ref={videoRef}
          playsInline
          muted
          className="w-full h-full object-cover"
        />
        <div className="absolute inset-6 rounded-2xl border-2 border-[var(--accent)]/70 pointer-events-none" />
      </div>

      <p className="mt-6 text-xs text-[var(--ivory-dim)] text-center leading-relaxed max-w-xs">
        {fault ?? "Line the QR code up inside the frame."}
      </p>

      {fault && (
        <div className="mt-4 flex items-start gap-2 text-[11px] text-[var(--ink-muted)] max-w-xs">
          <Info className="w-3.5 h-3.5 mt-0.5 shrink-0 text-[var(--accent)]" />
          <span>Your phone&rsquo;s own camera app will also open the link directly.</span>
        </div>
      )}
    </div>
  );
}
