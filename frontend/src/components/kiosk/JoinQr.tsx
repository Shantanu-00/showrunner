"use client";

import { useEffect, useState } from "react";
import QRCode from "qrcode";

/** The join-QR (spec 12 §6/§10) — generated entirely client-side (no third-party CDN, no
 * runtime network call) so it survives hostile venue Wi-Fi like every other kiosk asset. */
export function JoinQr({ url, sizePx }: { url: string; sizePx: number }) {
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
        className="skeleton-shimmer rounded-[var(--radius-card)]"
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
      className="rounded-[var(--radius-card)]"
      style={{ border: "var(--hairline)" }}
    />
  );
}
