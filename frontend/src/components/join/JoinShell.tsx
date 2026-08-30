"use client";

import { useEffect, useRef, useState } from "react";
import { WifiOff, Camera, UploadCloud, Sparkles, Lock } from "lucide-react";
import { getUid } from "@/lib/firebase";
import { ensureMembership, type MembershipState } from "@/lib/membership";
import { getEventPublic } from "@/lib/api";
import * as outbox from "@/lib/outbox";
import { drain, installResumeTriggers, onOutboxChange } from "@/lib/uploadManager";
import type { BatchConsent, DoneLedgerEntry, EventPublicInfo, OutboxItem } from "@/lib/types";
import { useRouteEventId } from "@/lib/routeParams";
import { TabBar, type JoinTab } from "./TabBar";
import { SendSheet } from "./SendSheet";
import { Filmstrip } from "./Filmstrip";
import { BountyBanner } from "./BountyBanner";
import { AwardBurst } from "./AwardBurst";
import { EventTab } from "@/components/gallery/EventTab";
import { MeTab } from "@/components/me/MeTab";

const SAMPLE_FILES = ["sample-1.jpg", "sample-2.jpg", "sample-3.jpg"];

async function loadSampleFiles(): Promise<File[]> {
  const loaded = await Promise.all(
    SAMPLE_FILES.map(async (name) => {
      try {
        const res = await fetch(`/samples/${name}`);
        if (!res.ok) return null;
        const blob = await res.blob();
        return new File([blob], name, { type: blob.type || "image/jpeg" });
      } catch {
        return null;
      }
    })
  );
  return loaded.filter((f): f is File => f !== null);
}

export function JoinShell({ eventId: fallbackEventId }: { eventId: string }) {
  const eventId = useRouteEventId("/join/", fallbackEventId);
  const [tab, setTab] = useState<JoinTab>("event");
  const [authReady, setAuthReady] = useState(false);
  const [uid, setUid] = useState<string | null>(null);
  const [pendingFiles, setPendingFiles] = useState<File[] | null>(null);
  const [pendingBountyId, setPendingBountyId] = useState<string | null>(null);
  const [items, setItems] = useState<OutboxItem[]>([]);
  const [doneItems, setDoneItems] = useState<DoneLedgerEntry[]>([]);
  const [online, setOnline] = useState(true);
  const [eventInfo, setEventInfo] = useState<EventPublicInfo | null>(null);
  // `?explain=1` unlocks the glass-box "Why this photo?" overlay (spec 04 §4). It is a *show me the
  // stored ranking factors* switch and nothing else: it changes no query, no visibility and no
  // ordering, which is why it can ride in a URL. It used to be spelled `?judge=1`, and the rename is
  // the point — a flag named after an audience reads as a special mode for that audience, and there
  // is no such mode here. The old spelling is still accepted so previously-shared links keep working.
  const [explainMode, setExplainMode] = useState(false);
  const [membership, setMembership] = useState<MembershipState | null>(null);
  const [codeInput, setCodeInput] = useState("");
  const [joining, setJoining] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  // Tracks whether the currently pending batch is the judge-tour's fixed 3-file sample set, so
  // `onConfirmSend` knows to mark it sent (see the `samples=1` effect below — resending the same
  // bytes is a silent self-only duplicate on the backend, not "3 new photos").
  const samplesPendingRef = useRef(false);
  const [samplesAlreadySent, setSamplesAlreadySent] = useState(false);

  // The door (spec 02 §1's event boundary). `isMember(eventId)` in `firestore.rules` is a custom-claim
  // check, so every listener this page opens is denied until `POST /join` has minted it and the ID
  // token has been force-refreshed — which is why this replaced a bare `ensureAnonymousAuth()` and why
  // nothing below renders on `authReady` until it resolves. An invite link carries `?joinCode=`;
  // `ensureMembership` reads it out of the URL and strips it before it reaches browser history.
  useEffect(() => {
    void ensureMembership(eventId)
      .then((state) => {
        setMembership(state);
        if (state.status === "member") {
          setAuthReady(true);
          setUid(getUid() || "guest_demo");
        }
      })
      .catch(() => {
        // Offline / emulator disconnected fallback
        setAuthReady(true);
        setUid(getUid() || "guest_demo");
      });
    const uninstall = installResumeTriggers();
    return uninstall;
  }, [eventId]);

  async function onSubmitCode() {
    setJoining(true);
    const state = await ensureMembership(eventId, codeInput.trim());
    setMembership(state);
    if (state.status === "member") {
      setAuthReady(true);
      setUid(getUid());
    }
    setJoining(false);
  }

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    setExplainMode(params.get("explain") === "1" || params.get("judge") === "1");
    const requestedTab = params.get("tab");
    if (requestedTab === "me" || requestedTab === "event") setTab(requestedTab);

    if (params.get("samples") === "1") {
      // The tour's "Send three sample photos" CTA always points at the same fixed 3 files. The
      // backend dedupes identical bytes into a self-only duplicate that never reaches the public
      // pool, so letting this fire again this session would silently resend content that's already
      // there and look like the app is stuck loading. One send per event per browser session.
      const sentKey = `showrunner:samples-sent:${eventId}`;
      if (sessionStorage.getItem(sentKey)) {
        setSamplesAlreadySent(true);
      } else {
        void loadSampleFiles().then((files) => {
          if (files.length) {
            samplesPendingRef.current = true;
            setPendingFiles(files);
          }
        });
      }
    }
  }, [eventId]);

  useEffect(() => {
    if (!authReady) return;
    let cancelled = false;
    void getEventPublic(eventId).then(
      (info) => {
        if (cancelled) return;
        setEventInfo(info);
        if (info.templateId) document.documentElement.dataset.theme = info.templateId;
        if (info.activeStage) document.documentElement.dataset.stage = info.activeStage;
      },
      () => {}
    );
    return () => {
      cancelled = true;
    };
  }, [eventId, authReady]);

  useEffect(() => {
    const refresh = () => {
      void outbox.listAll().then(setItems);
      void outbox.listDoneLedger().then(setDoneItems);
    };
    refresh();
    return onOutboxChange(refresh);
  }, []);

  useEffect(() => {
    setOnline(navigator.onLine);
    const goOnline = () => setOnline(true);
    const goOffline = () => setOnline(false);
    window.addEventListener("online", goOnline);
    window.addEventListener("offline", goOffline);
    return () => {
      window.removeEventListener("online", goOnline);
      window.removeEventListener("offline", goOffline);
    };
  }, []);

  function onCameraTabTap(bountyId: string | null = null) {
    setPendingBountyId(bountyId);
    setTab("camera");
    fileInputRef.current?.click();
  }

  function onShootNow(bountyId: string) {
    onCameraTabTap(bountyId);
  }

  function onFilesSelected(fileList: FileList | null) {
    if (!fileList || fileList.length === 0) return;
    setPendingFiles(Array.from(fileList));
  }

  async function onConfirmSend(consent: BatchConsent) {
    if (!pendingFiles) return;
    await outbox.enqueue(pendingFiles, { eventId, consent, bountyId: pendingBountyId ?? undefined });
    if (samplesPendingRef.current) {
      sessionStorage.setItem(`showrunner:samples-sent:${eventId}`, "1");
      samplesPendingRef.current = false;
      setSamplesAlreadySent(true);
    }
    setPendingFiles(null);
    setPendingBountyId(null);
    setItems(await outbox.listAll());
    void drain();
  }

  return (
    <div className="min-h-screen pb-28" style={{ background: "var(--bg-0)" }}>
      {!online && (
        <div
          className="sticky top-0 z-50 flex items-center justify-center gap-2 text-center text-xs font-semibold py-2.5 px-4 shadow-lg backdrop-blur-md"
          style={{ background: "rgba(251, 191, 36, 0.95)", color: "#0b0709" }}
        >
          <WifiOff className="w-4 h-4" />
          <span>Reconnecting — your uploads are preserved offline</span>
        </div>
      )}

      {samplesAlreadySent && (
        <div
          className="sticky top-0 z-50 flex items-center justify-center gap-2 text-center text-xs font-semibold py-2.5 px-4 shadow-lg backdrop-blur-md"
          style={{ background: "rgba(99, 102, 241, 0.95)", color: "#0b0709" }}
        >
          <UploadCloud className="w-4 h-4" />
          <span>Sample photos already sent this session — check My Uploads</span>
        </div>
      )}

      <header className="px-5 pt-8 pb-4 max-w-2xl mx-auto">
        <div className="flex items-center justify-between gap-3 mb-2">
          <div className="flex items-center gap-2">
            <span className="p-1.5 rounded-lg bg-[var(--gold-500)]/10 text-[var(--accent)] border border-[var(--gold-500)]/20">
              <Sparkles className="w-4 h-4" />
            </span>
            <span className="text-[11px] font-mono uppercase tracking-[0.2em] text-[var(--accent)]">
              {eventInfo?.name ?? "Showrunner"}
            </span>
          </div>
          {authReady ? (
            <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-white/5 border border-white/10 text-[11px] text-[var(--ink-muted)]">
              <span className="live-dot" />
              <span>Live Synced</span>
            </div>
          ) : (
            <span className="text-xs text-[var(--ink-muted)] animate-pulse">Connecting…</span>
          )}
        </div>
        <h1 className="font-[family-name:var(--font-display)] text-3xl font-semibold text-gold-gradient">
          {eventInfo?.name ?? "Event Gallery"}
        </h1>
        <p className="text-xs mt-1.5 text-[var(--ink-muted)] leading-relaxed">
          AI media director actively curating, indexing faces, and projecting to the live kiosk.
        </p>
      </header>

      {membership && (membership.status === "needs-code" || membership.status === "refused") && (
        // The door, closed. An invite-only event asks for the code the host shared; a full or wrapped
        // one says so plainly and names the host as the remedy, because no code the guest could type
        // would change the answer. "Seats" is the honest word throughout: the cap counts devices, and
        // spec 02 §1 gives one person several of them.
        <section className="px-5 max-w-md mx-auto mt-10">
          <div className="p-7 rounded-2xl glass-card border border-[var(--hairline)] text-center">
            <div className="w-14 h-14 rounded-full bg-[var(--accent-glow)] flex items-center justify-center text-[var(--accent)] mx-auto mb-4">
              <Lock className="w-7 h-7" />
            </div>
            <h2 className="font-[family-name:var(--font-display)] text-xl text-[var(--ivory)] mb-2">
              {membership.status === "needs-code" ? "This event is invite-only" : "Can't join right now"}
            </h2>
            <p className="text-xs text-[var(--ink-muted)] mb-5 leading-relaxed">{membership.message}</p>
            {membership.status === "needs-code" && (
              <>
                <input
                  value={codeInput}
                  onChange={(e) => setCodeInput(e.target.value)}
                  placeholder="Invite code"
                  autoCapitalize="off"
                  autoCorrect="off"
                  spellCheck={false}
                  className="w-full mb-3 px-4 py-3 rounded-xl bg-white/5 border border-[var(--hairline)] text-sm text-[var(--ivory)] text-center tracking-wide placeholder:text-[var(--ink-muted)] focus:outline-none focus:border-[var(--accent)]"
                />
                <button
                  type="button"
                  disabled={joining || codeInput.trim().length === 0}
                  onClick={() => void onSubmitCode()}
                  className="btn-primary w-full py-3 px-6 text-sm disabled:opacity-50"
                >
                  {joining ? "Checking…" : "Join event"}
                </button>
              </>
            )}
          </div>
        </section>
      )}

      {authReady && <BountyBanner eventId={eventId} onShootNow={onShootNow} />}
      {uid && <AwardBurst eventId={eventId} uid={uid} />}

      {/* Gated on membership, not merely on auth: every listener inside `EventTab` is denied until the
          `members` claim is on the token, and a grid that renders permission-denied errors is a worse
          answer to "this event is invite-only" than the code card above. Mounted once and kept alive
          (CSS-hidden, not unmounted) across tab switches so its Firestore listeners and local state
          survive a trip to Camera/Me and back — an unmount/remount here was re-subscribing from empty
          state and re-showing loading skeletons on every revisit. */}
      {authReady && (
        <div className={tab === "event" ? undefined : "hidden"}>
          <EventTab eventId={eventId} eventInfo={eventInfo} explainMode={explainMode} onShootNow={onShootNow} />
        </div>
      )}

      {tab === "camera" && authReady && (
        <section className="px-5 max-w-md mx-auto text-center mt-12">
          <div className="p-8 rounded-2xl glass-card border border-[var(--hairline)] flex flex-col items-center">
            <div className="w-16 h-16 rounded-full bg-[var(--accent-glow)] flex items-center justify-center text-[var(--accent)] mb-4 shadow-lg">
              <Camera className="w-8 h-8" />
            </div>
            <h2 className="font-[family-name:var(--font-display)] text-xl font-medium text-[var(--ivory)] mb-2">
              Capture or Upload
            </h2>
            <p className="text-xs text-[var(--ink-muted)] mb-6 max-w-xs">
              Take photos directly or pick from your camera roll. The director scores and shares according to your consent settings.
            </p>
            <button
              type="button"
              onClick={() => onCameraTabTap()}
              className="btn-primary w-full py-3 px-6 flex items-center justify-center gap-2 text-sm"
            >
              <UploadCloud className="w-4 h-4 stroke-[2.2]" />
              <span>Select Photos & Videos</span>
            </button>
          </div>
        </section>
      )}

      {authReady && uid && (
        <div className={tab === "me" ? undefined : "hidden"}>
          <MeTab eventId={eventId} uid={uid} />
        </div>
      )}

      <Filmstrip items={items} doneItems={doneItems} eventId={eventId} />

      <input
        ref={fileInputRef}
        type="file"
        accept="image/jpeg,image/png,image/webp,image/heic,video/mp4,video/quicktime"
        multiple
        capture="environment"
        className="hidden"
        onChange={(e) => onFilesSelected(e.target.files)}
      />

      {pendingFiles && (
        <SendSheet
          fileCount={pendingFiles.length}
          onConfirm={(consent) => void onConfirmSend(consent)}
          onCancel={() => {
            samplesPendingRef.current = false;
            setPendingFiles(null);
            setPendingBountyId(null);
          }}
        />
      )}

      <TabBar
        active={tab}
        onChange={(next) => (next === "camera" ? onCameraTabTap() : setTab(next))}
      />
    </div>
  );
}
