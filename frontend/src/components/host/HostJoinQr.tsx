"use client";

import { useEffect, useState } from "react";
import QRCode from "qrcode";

/** A small client-side QR render for the wizard's Links step — same `qrcode` package the kiosk's
 * own `JoinQr` uses, kept as a separate minimal component so the host wizard's bundle never imports
 * from `components/kiosk/` (a different lane's surface, spec 12 §5.1's code-split boundary). */
export function HostJoinQr({ url, sizePx = 176 }: { url: string; sizePx?: number }) {
  const [dataUrl, setDataUrl] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void QRCode.toDataURL(url, {
      width: sizePx,
      margin: 1,
      color: { dark: "#0B0709", light: "#F5EFE6" },
    }).then((d) => {
      if (!cancelled) setDataUrl(d);
    });
    return () => {
      cancelled = true;
    };
  }, [url, sizePx]);

  if (!dataUrl) {
    return (
      <div
        className="skeleton-shimmer rounded-2xl bg-white/5"
        style={{ width: sizePx, height: sizePx }}
      />
    );
  }

  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={dataUrl}
      width={sizePx}
      height={sizePx}
      alt="Scan to join the event"
      className="rounded-2xl border border-white/10"
    />
  );
}
