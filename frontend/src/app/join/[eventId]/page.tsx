import { JoinShell } from "@/components/join/JoinShell";

export function generateStaticParams() {
  // Static export needs at least one known param; real events are created at runtime,
  // so the client shell handles any eventId via the dynamic route regardless.
  return [{ eventId: process.env.NEXT_PUBLIC_DEFAULT_EVENT_ID ?? "judge_demo" }];
}

export default async function JoinPage({
  params,
}: {
  params: Promise<{ eventId: string }>;
}) {
  const { eventId } = await params;
  return <JoinShell eventId={eventId} />;
}
