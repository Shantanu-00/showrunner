"use client";

import { useEffect, useRef, useState } from "react";
import { ensureAnonymousAuth } from "@/lib/firebase";
import * as outbox from "@/lib/outbox";
import { drain, installResumeTriggers, onOutboxChange } from "@/lib/uploadManager";
import type { BatchConsent, OutboxItem } from "@/lib/types";
import { TabBar, type JoinTab } from "./TabBar";
import { SendSheet } from "./SendSheet";
import { Filmstrip } from "./Filmstrip";

export function JoinShell({ eventId }: { eventId: string }) {
  const [tab, setTab] = useState<JoinTab>("event");
  const [authReady, setAuthReady] = useState(false);
  const [pendingFiles, setPendingFiles] = useState<File[] | null>(null);
  const [items, setItems] = useState<OutboxItem[]>([]);
  const [online, setOnline] = useState(true);
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    void ensureAnonymousAuth().then(() => setAuthReady(true));
    const uninstall = installResumeTriggers();
    return uninstall;
  }, []);

  useEffect(() => {
    const refresh = () => void outbox.listAll().then(setItems);
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

  function onCameraTabTap() {
    setTab("camera");
    fileInputRef.current?.click();
  }

  function onFilesSelected(fileList: FileList | null) {
    if (!fileList || fileList.length === 0) return;
    setPendingFiles(Array.from(fileList));
  }

  async function onConfirmSend(consent: BatchConsent) {
    if (!pendingFiles) return;
    await outbox.enqueue(pendingFiles, { eventId, consent });
    setPendingFiles(null);
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

      {tab === "event" && (
        <section className="px-5">
          <p className="text-center mt-16" style={{ color: "var(--ink-muted)" }}>
            The kiosk is waiting for its first photo. Scan, shoot, make history.
          </p>
        </section>
      )}

      {tab === "camera" && (
        <section className="px-5">
          <p className="text-center mt-16" style={{ color: "var(--ink-muted)" }}>
            Tap the camera tab again to pick more photos.
          </p>
        </section>
      )}

      {tab === "me" && (
        <section className="px-5">
          <p className="text-center mt-16" style={{ color: "var(--ink-muted)" }}>
            Take a selfie and every photo of you finds its way here.
          </p>
        </section>
      )}

      <Filmstrip items={items} />

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
          onCancel={() => setPendingFiles(null)}
        />
      )}

      <TabBar
        active={tab}
        onChange={(next) => (next === "camera" ? onCameraTabTap() : setTab(next))}
      />
    </div>
  );
}
