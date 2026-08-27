import { KioskShell } from "@/components/kiosk/KioskShell";

export function generateStaticParams() {
  // Static export needs at least one known param; the client shell handles any eventId
  // dynamically at runtime regardless (same pattern as /join/[eventId]).
  return [{ eventId: process.env.NEXT_PUBLIC_DEFAULT_EVENT_ID ?? "pune_wedding_2026" }];
}

export default async function KioskPage({
  params,
}: {
  params: Promise<{ eventId: string }>;
}) {
  const { eventId } = await params;
  return <KioskShell eventId={eventId} />;
}
