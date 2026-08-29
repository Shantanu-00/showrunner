"use client";

import { useRef, useState } from "react";
import { ShieldAlert, ShieldCheck } from "lucide-react";
import { setFreeze } from "@/lib/hostApi";

const HOLD_MS = 600;

export function FreezeButton({
  eventId,
  frozen,
  onChanged,
}: {
  eventId: string;
  frozen: boolean;
  onChanged: (frozen: boolean) => void;
}) {
  const [holding, setHolding] = useState(false);
  const [busy, setBusy] = useState(false);
  const timerRef = useRef<number | null>(null);

  function cancel() {
    if (timerRef.current) window.clearTimeout(timerRef.current);
    timerRef.current = null;
    setHolding(false);
  }

  function start() {
    if (busy) return;
    setHolding(true);
    timerRef.current = window.setTimeout(() => {
      void trigger();
    }, HOLD_MS);
  }

  async function trigger() {
    setHolding(false);
    setBusy(true);
    try {
      const res = await setFreeze(eventId, !frozen);
      onChanged(res.publicFrozen);
    } finally {
      setBusy(false);
    }
  }

  return (
    <button
      type="button"
      onMouseDown={start}
      onMouseUp={cancel}
      onMouseLeave={cancel}
      onTouchStart={start}
      onTouchEnd={cancel}
      disabled={busy}
      className={`relative overflow-hidden text-xs font-semibold px-4 py-2.5 rounded-full flex items-center gap-2 shadow-lg transition-all select-none ${
        frozen
          ? "bg-[var(--gold-500)] text-black border-2 border-[var(--gold-300)]"
          : "bg-[var(--danger)] text-white hover:brightness-110"
      }`}
      title="Hold 600ms to confirm"
    >
      {holding && (
        <span
          className="absolute inset-0 origin-left pointer-events-none"
          style={{
            background: "rgba(255, 255, 255, 0.4)",
            animation: `hold-fill ${HOLD_MS}ms linear forwards`,
          }}
        />
      )}
      <span className="relative flex items-center gap-1.5">
        {frozen ? (
          <>
            <ShieldCheck className="w-4 h-4 stroke-[2.5]" />
            <span>Unfreeze Wall (Public is Frozen)</span>
          </>
        ) : busy ? (
          <span>Updating…</span>
        ) : (
          <>
            <ShieldAlert className="w-4 h-4 stroke-[2.5]" />
            <span>Freeze Public Wall</span>
          </>
        )}
      </span>
      <style jsx>{`
        @keyframes hold-fill {
          from {
            transform: scaleX(0);
          }
          to {
            transform: scaleX(1);
          }
        }
      `}</style>
    </button>
  );
}
