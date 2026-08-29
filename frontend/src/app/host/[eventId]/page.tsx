import { HostConsoleShell } from "@/components/host/HostConsoleShell";

export function generateStaticParams() {
  // Same reasoning as /join and /kiosk (spec 09 §1): static export bakes one HTML file per
  // dynamic route at build time; useRouteEventId recovers the real eventId client-side for
  // every other event once `firebase.json`'s /host/** rewrite lands the client here.
  return [{ eventId: process.env.NEXT_PUBLIC_DEFAULT_EVENT_ID ?? "judge_demo" }];
}

export default async function HostConsolePage({
  params,
}: {
  params: Promise<{ eventId: string }>;
}) {
  const { eventId } = await params;
  return <HostConsoleShell eventId={eventId} />;
}
