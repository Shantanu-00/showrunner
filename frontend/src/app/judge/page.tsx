"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

// This URL has already been handed out, so it has to keep resolving; the walkthrough itself moved to
// `/how-it-works`, which is what it actually is. Static export can't do a server redirect, so this
// is a client-side replace — `replace`, not `push`, so the old path leaves no history entry.
export default function JudgePage() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/how-it-works");
  }, [router]);
  return null;
}
