import { ClaimShell } from "@/components/claim/ClaimShell";

export function generateStaticParams() {
  // Pinned, deliberately not `NEXT_PUBLIC_DEFAULT_EVENT_ID`. This id only ever names the *file* the
  // static export writes, and `firebase.json` rewrites every real event id onto that one file — so
  // when the two disagree (a machine whose .env said `judge_demo`, a rewrite that still said
  // `global_demo`) the rewrite points at a file the build never produced and the whole route 404s on
  // deploy. The shell reads the actual event id from the URL at runtime (`lib/routeParams.ts`).
  return [{ eventId: "global_demo" }];
}

export default async function ClaimPage({
  params,
}: {
  params: Promise<{ eventId: string }>;
}) {
  const { eventId } = await params;
  return <ClaimShell eventId={eventId} />;
}
