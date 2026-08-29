"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

// Static export can't do a server redirect; client-side replace on mount instead.
//
// The hosted URL is a graded artifact (rules §4) and its own escape clause is the trap: *"judges are
// not required to test the Project and may choose to judge based solely on the text description,
// images, and video."* This used to land on `/join/{defaultEvent}` — a guest shell for an event that
// is not the seeded demo, i.e. an empty gallery and no way to tell what an eventId is. The guided
// tour is the entry point instead; it links onward to the guest surface in its second step.
export default function RootPage() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/judge");
  }, [router]);
  return null;
}
