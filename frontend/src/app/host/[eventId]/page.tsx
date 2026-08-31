import { HostConsoleShell } from "@/components/host/HostConsoleShell";

export function generateStaticParams() {
  // Same reasoning as /join and /kiosk (spec 09 §1): static export bakes one HTML file per
  // dynamic route at build time; useRouteEventId recovers the real eventId client-side for
  // every other event once `firebase.json`'s /host/** rewrite lands the client here.
  // Pinned, deliberately not `NEXT_PUBLIC_DEFAULT_EVENT_ID`. This id only ever names the *file* the
  // static export writes, and `firebase.json` rewrites every real event id onto that one file — so
  // when the two disagree (a machine whose .env said `global_demo`, a rewrite that still said
  // `global_demo`) the rewrite points at a file the build never produced and the whole route 404s on
  // deploy. The shell reads the actual event id from the URL at runtime (`lib/routeParams.ts`).
  return [{ eventId: "global_demo" }];
}

export default async function HostConsolePage({
  params,
}: {
  params: Promise<{ eventId: string }>;
}) {
  const { eventId } = await params;
  return <HostConsoleShell eventId={eventId} />;
}
