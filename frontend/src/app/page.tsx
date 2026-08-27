"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

// Static export can't do a server redirect; client-side replace on mount instead.
export default function RootPage() {
  const router = useRouter();
  useEffect(() => {
    const defaultEventId = process.env.NEXT_PUBLIC_DEFAULT_EVENT_ID ?? "pune_wedding_2026";
    router.replace(`/join/${defaultEventId}`);
  }, [router]);
  return null;
}
