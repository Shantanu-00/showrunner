import { ClaimShell } from "@/components/claim/ClaimShell";

export function generateStaticParams() {
  return [{ eventId: process.env.NEXT_PUBLIC_DEFAULT_EVENT_ID ?? "judge_demo" }];
}

export default async function ClaimPage({
  params,
}: {
  params: Promise<{ eventId: string }>;
}) {
  const { eventId } = await params;
  return <ClaimShell eventId={eventId} />;
}
