"use client";

import { useEffect, useRef, useState } from "react";
import { ensureAnonymousAuth, getUid } from "@/lib/firebase";
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

/** The three photos bundled for the `/judge` tour. Real JPEGs in `frontend/public/samples/`, fetched
 * as blobs so they enter the pipeline byte-identically to a phone upload — same signed PUT, same
 * intake, same Curator. They carry no EXIF capture time, which is itself honest: `intake` falls back
 * to arrival time and `shared/pipeline.py` marks `exifMissing`, so the Curator's temporal prior goes
 * flat at 0.5 exactly as it would for a stripped WhatsApp forward. */
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
  const [judgeMode, setJudgeMode] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    void ensureAnonymousAuth().then(() => {
      setAuthReady(true);
      setUid(getUid());
    });
    const uninstall = installResumeTriggers();
    return uninstall;
  }, []);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    setJudgeMode(params.get("judge") === "1");
    const requestedTab = params.get("tab"); // e.g. the /claim redemption landing on Me
    if (requestedTab === "me" || requestedTab === "event") setTab(requestedTab);

    // `?samples=1` — the `/judge` tour's step 3 (spec 09 §4's "3 sample photos ready to upload").
    // A desktop browser has no camera and a judge should not have to find three photos of their own,
    // so the samples ship with the page and arrive here as ordinary `File`s. Deliberately routed
    // through `setPendingFiles`, which is what the file input does: the SendSheet opens, the judge
    // makes the *real* consent choice (moment C1), and the ordinary outbox/drain path carries them.
    // Anything that skipped the send sheet would also skip the consent decision, which is the single
    // most important thing this step exists to show.
    if (params.get("samples") === "1") {
      void loadSampleFiles().then((files) => {
        if (files.length) setPendingFiles(files);
      });
    }
  }, []);

  // Spec 12 §3: `data-theme`/`data-stage` on <html> retune every open surface with a pure
  // CSS-variable swap — no reload, no re-render. One REST call at load (this is deliberately
  // NOT a listener/poll — the theme flip demo beat lives on the kiosk, which does listen live).
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

  /** Spec 05 §3: "guest taps banner → camera → upload flows the normal pipeline with `bountyId`
   * stamped at intent time." `bountyId` is set (or cleared) *before* the OS picker opens, not
   * after a file comes back — some browsers never fire the file input's `onChange` at all when
   * the picker is cancelled, so clearing on that event would leave a stale bounty attached to
   * whatever the guest shoots next through the ordinary camera tab. */
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
    setPendingFiles(null);
    setPendingBountyId(null);
    setItems(await outbox.listAll());
    void drain();
  }

  return (
    <div className="min-h-screen pb-20" style={{ background: "var(--bg-0)" }}>
      {!online && (
        <div
          className="sticky top-0 z-40 text-center text-sm py-2"
          style={{ background: "var(--warn)", color: "var(--bg-0)" }}
        >
          📶 reconnecting — your uploads are safe
        </div>
      )}

      <header className="px-5 pt-8 pb-4">
        <h1 className="font-[var(--font-display)] text-3xl" style={{ color: "var(--ivory)" }}>
          Showrunner
        </h1>
        <p className="text-sm mt-2" style={{ color: "var(--ink-muted)" }}>
          Photos you share go to the couple&rsquo;s album and to people who appear in
          them. Public display is always your choice per upload.
        </p>
        {!authReady && (
          <p className="text-xs mt-2" style={{ color: "var(--ink-muted)" }}>
            Joining the event…
          </p>
        )}
      </header>

      {authReady && <BountyBanner eventId={eventId} onShootNow={onShootNow} />}
      {uid && <AwardBurst eventId={eventId} uid={uid} />}

      {tab === "event" && (
        <EventTab eventId={eventId} eventInfo={eventInfo} judgeMode={judgeMode} onShootNow={onShootNow} />
      )}

      {tab === "camera" && (
        <section className="px-5">
          <p className="text-center mt-16" style={{ color: "var(--ink-muted)" }}>
            Tap the camera tab again to pick more photos.
          </p>
        </section>
      )}

      {tab === "me" && authReady && uid && <MeTab eventId={eventId} uid={uid} />}

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
