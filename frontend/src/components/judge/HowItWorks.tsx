"use client";

import { useCallback, useEffect, useState } from "react";
import {
  Sparkles,
  Tv,
  Smartphone,
  Upload,
  Clock,
  ShieldCheck,
  ExternalLink,
  Terminal,
  ArrowRight,
  Target,
  ScanFace,
  Film,
  Lock,
} from "lucide-react";
import { ensureAnonymousAuth } from "@/lib/firebase";
import { getEventPublic, warmup, forceDemoTick } from "@/lib/api";
import type { EventPublicInfo } from "@/lib/types";
import { TickCountdownView } from "@/components/host/TickCountdown";
import { DisclosurePanel } from "./DisclosurePanel";

// The live event this page opens. `NEXT_PUBLIC_JUDGE_EVENT_ID` predates
// `NEXT_PUBLIC_DEFAULT_EVENT_ID` and stays supported so an already-deployed environment keeps
// working; the default-event id is the fallback so a fresh checkout needs one variable, not two.
const EVENT_ID =
  process.env.NEXT_PUBLIC_JUDGE_EVENT_ID ??
  process.env.NEXT_PUBLIC_DEFAULT_EVENT_ID ??
  "judge_demo";
const PROJECT = process.env.NEXT_PUBLIC_GCP_PROJECT ?? "showrunner-hq";
const REPO = process.env.NEXT_PUBLIC_REPO_URL ?? "https://github.com/Shantanu-00/showrunner";
const VIDEO = process.env.NEXT_PUBLIC_VIDEO_URL ?? "";

const CONSOLE_LINKS: Array<{ label: string; note: string; href: string }> = [
  {
    label: "Cloud Run — seven services",
    note: "us-central1, active revisions, health checks green",
    href: `https://console.cloud.google.com/run?project=${PROJECT}`,
  },
  {
    label: "Cloud Scheduler — director-tick",
    note: "The cron that wakes the Story Director on its own",
    href: `https://console.cloud.google.com/cloudscheduler?project=${PROJECT}`,
  },
  {
    label: "Logs Explorer — the live pipeline",
    note: "stage=curate media=… ms=1180 tokens_in=1548",
    href: `https://console.cloud.google.com/logs/query?project=${PROJECT}`,
  },
  {
    label: "act.py — how a bounty is chosen",
    note: "Deterministic story planning, readable in one file",
    href: `${REPO}/blob/main/backend/directors/story/act.py`,
  },
];

const PIPELINE: Array<{ icon: React.ElementType; title: string; body: string }> = [
  {
    icon: Sparkles,
    title: "The Curator reads the photo",
    body:
      "A multimodal model scores composition and moment, writes the caption you see under it on the wall, and tags which part of the evening it belongs to.",
  },
  {
    icon: ScanFace,
    title: "The Face Indexer finds people",
    body:
      "Faces are embedded so a guest's private album can fill itself. Only guests who opted in are identified, the index never leaves the event, and it dies with it.",
  },
  {
    icon: ShieldCheck,
    title: "The Guardian screens for dignity",
    body:
      "Anything undignified, unflattering or off-limits for the culture of this event never reaches a public surface — the gray zone goes to the host, not to the wall.",
  },
  {
    icon: Tv,
    title: "The Publisher runs the show",
    body:
      "A ranked playlist rebuilds continuously and the big screen follows it. A photo taken in the room is on the wall in a couple of seconds.",
  },
  {
    icon: Target,
    title: "The Story Director spots what's missing",
    body:
      "It keeps a ledger of the moments this kind of event needs and, when one is uncovered, posts a mission to guests' phones for the shot nobody took.",
  },
  {
    icon: Film,
    title: "The Reel Director cuts the film",
    body:
      "Beat-matched reels with an originally composed soundtrack, premiered on the big screen while the event is still happening.",
  },
];

export function HowItWorks() {
  const [info, setInfo] = useState<EventPublicInfo | null>(null);
  const [forcing, setForcing] = useState(false);
  const [forced, setForced] = useState<string | null>(null);

  const load = useCallback(() => {
    void getEventPublic(EVENT_ID).then(setInfo, () => {});
  }, []);

  useEffect(() => {
    void ensureAnonymousAuth().then(() => {
      load();
      // `worker-face` carries a 326 MB model and cold-starts slowly; warming it here is the
      // difference between a first upload taking seconds and taking most of a minute.
      void warmup();
    });
  }, [load]);

  useEffect(() => {
    if (info?.templateId) document.documentElement.dataset.theme = info.templateId;
    if (info?.activeStage) document.documentElement.dataset.stage = info.activeStage;
  }, [info]);

  async function onForce() {
    setForcing(true);
    setForced(null);
    try {
      const res = await forceDemoTick(EVENT_ID);
      setForced(res.ran ? "Tick executed — watch the guest phone for a new mission." : (res.message ?? "Already evaluating state."));
      load();
    } catch {
      setForced("The scheduled tick is running in the background regardless.");
    } finally {
      setForcing(false);
    }
  }

  const director = info?.director;
  // `?explain=1` is what `JoinShell` reads to unlock the glass-box "Why this photo?" overlay on the
  // public gallery — an *explain-the-ranking* switch, not a behaviour modifier: it changes no query,
  // no visibility and no ordering, only whether the stored score factors are drawn. `JoinShell` still
  // accepts the older `?judge=1` spelling so shared links keep working, but nothing generates it.
  const joinBase = `/join/${EVENT_ID}?explain=1`;

  return (
    <main className="min-h-screen px-5 py-14 mx-auto max-w-3xl">
      <header className="mb-10">
        <a
          href="/"
          className="text-[11px] font-mono tracking-[0.2em] uppercase font-bold text-[var(--ink-muted)] hover:text-[var(--accent)] transition-colors"
        >
          ← SHOWRUNNER
        </a>
        <h1 className="font-[family-name:var(--font-display)] text-4xl sm:text-5xl font-semibold leading-[1.1] text-gold-gradient mt-4 mb-5">
          How it works
        </h1>
        <p className="text-sm sm:text-base text-[var(--ivory-dim)] leading-relaxed max-w-xl">
          Showrunner is an autonomous media director for a live event. Guests photograph the evening
          on their own phones; a fleet of agents curates what arrives, decides what the room should
          see, gives every guest a private album of themselves, and asks for the shots nobody
          captured — while the event is still happening.
        </p>

        {info?.exists === false && (
          <div className="mt-5 p-4 rounded-2xl bg-[var(--warn)]/15 border border-[var(--warn)]/30 text-xs text-[var(--warn)] leading-relaxed">
            The open event below is resetting right now. Links stay valid — give it a minute and
            reload.
          </div>
        )}
      </header>

      {/* ---------------------------------------------------------------- try it */}
      <section className="mb-12">
        <h2 className="font-[family-name:var(--font-display)] text-2xl font-semibold text-[var(--ivory)] mb-1">
          Try it on a live event
        </h2>
        <p className="text-xs text-[var(--ink-muted)] mb-5 leading-relaxed">
          There is a real event running right now on the real deployment. Open the big screen on one
          device and the guest phone on another — a photo you send appears on the wall in seconds.
        </p>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-5">
          <TickCountdownView
            lastTickAtMs={director?.lastTickAt ? Date.parse(director.lastTickAt) : null}
            tickCount={director?.tickCount ?? 0}
            cadenceSec={director?.cadenceSec ?? 120}
            onDue={load}
          />
          <div className="rounded-2xl p-4 glass-card border border-white/10 flex flex-col justify-between">
            <div className="flex items-center justify-between mb-1">
              <span className="text-[11px] font-medium text-[var(--ink-muted)]">Event status</span>
              <span className="live-dot" />
            </div>
            <p className="font-mono text-2xl font-bold uppercase text-[var(--ivory)]">
              {info?.status ?? "Live"}
            </p>
            <p className="text-[11px] text-[var(--gold-300)] font-mono mt-1">
              {info?.activeStage ? `Now: ${info.activeStage}` : "Running unattended"}
            </p>
          </div>
        </div>

        <ol className="space-y-3">
          <Step
            n={1}
            icon={Tv}
            title="Put the show on a big screen"
            href={`/kiosk/${EVENT_ID}`}
            cta="Open the wall"
          >
            The screen a venue would project. Press <em>Start show</em> for fullscreen and sound.
            Everything on it — the running order, the captions, the countdowns — was decided by the
            agents, not scheduled by a person.
          </Step>

          <Step
            n={2}
            icon={Smartphone}
            title="Join as a guest on your phone"
            href={joinBase}
            cta="Open the guest app"
          >
            Two taps from the QR code to being in. No install, no sign-up, no email address — the
            guest app signs you in silently and anonymously, because asking a guest to register is
            how you get zero photos.
          </Step>

          <Step
            n={3}
            icon={Upload}
            title="Send a few photos and watch them move"
            href={`${joinBase}&samples=1`}
            cta="Send three sample photos"
          >
            Each thumbnail carries the real state of its own pipeline —{" "}
            <strong className="text-[var(--ivory)]">uploading</strong> →{" "}
            <strong className="text-[var(--ivory)]">the Curator is judging your shot</strong> →{" "}
            <strong className="text-[var(--ok)] font-mono">live on the wall</strong> — usually about
            six seconds end to end.
          </Step>

          <Step
            n={4}
            icon={Target}
            title="Wait for a mission to arrive"
          >
            Nobody presses anything for this one. The countdown above is a real cron job; when the
            Story Director finds a moment with no coverage, a mission banner appears on the guest
            phone and a wanted poster takes over the wall. Shoot it and the points land with a
            confetti burst.
          </Step>

          <Step
            n={5}
            icon={Lock}
            title="Check what you can and can't do to someone else's photo"
            href={joinBase}
            cta="Back to the guest app"
          >
            Every batch is shared into a ring the sender picks at send time, every photo can be
            pulled back afterwards, and anyone who appears in a shot can remove themselves from the
            public wall in one tap. This is the part worth poking at.
          </Step>

          <Step
            n={6}
            icon={ShieldCheck}
            title="Look at the host's side"
            href="/host"
            cta="Create your own event"
          >
            The host console carries the review queues, the live aggregates, the stage overrides and
            a hold-to-confirm <strong>Freeze public</strong> switch that clears every public surface
            in about two seconds. Creating an event of your own takes under a minute and needs no
            account.
          </Step>
        </ol>
      </section>

      {/* ---------------------------------------------------------------- the fleet */}
      <section className="mb-12">
        <h2 className="font-[family-name:var(--font-display)] text-2xl font-semibold text-[var(--ivory)] mb-1">
          Who does what
        </h2>
        <p className="text-xs text-[var(--ink-muted)] mb-5 leading-relaxed">
          Six agents, each with one job and its own service. A photo passes through them in order.
        </p>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {PIPELINE.map((p) => (
            <div
              key={p.title}
              className="p-5 rounded-2xl glass-card border border-white/10 flex flex-col gap-2"
            >
              <div className="flex items-center gap-2">
                <p.icon className="w-4 h-4 text-[var(--accent)]" />
                <h3 className="font-[family-name:var(--font-display)] text-base font-medium text-[var(--ivory)]">
                  {p.title}
                </h3>
              </div>
              <p className="text-xs text-[var(--ink-muted)] leading-relaxed">{p.body}</p>
            </div>
          ))}
        </div>
      </section>

      {/* ---------------------------------------------------------------- honesty */}
      <section className="mb-12">
        <h2 className="font-[family-name:var(--font-display)] text-2xl font-semibold text-[var(--ivory)] mb-1">
          Verify it rather than take our word for it
        </h2>
        <p className="text-xs text-[var(--ink-muted)] mb-5 leading-relaxed">
          Every number on every screen traces to a stored aggregate. Here is where the machinery is
          visible from outside.
        </p>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 mb-5">
          {CONSOLE_LINKS.map((l) => (
            <a
              key={l.href}
              href={l.href}
              target="_blank"
              rel="noreferrer"
              className="p-3.5 rounded-xl glass-card border border-white/10 hover:border-[var(--accent)] transition-all block group"
            >
              <span className="text-xs font-semibold text-[var(--ivory)] group-hover:text-[var(--accent)] flex items-center justify-between gap-2">
                <span>{l.label}</span>
                <ExternalLink className="w-3.5 h-3.5 opacity-60 group-hover:opacity-100 shrink-0" />
              </span>
              <span className="text-[11px] text-[var(--ink-muted)] block mt-0.5 leading-relaxed">
                {l.note}
              </span>
            </a>
          ))}
        </div>

        <div className="p-6 rounded-3xl glass-card border border-white/10 shadow-xl">
          <div className="flex items-center gap-2 mb-2 text-[var(--gold-300)] font-semibold text-xs uppercase tracking-wider">
            <Terminal className="w-4 h-4" />
            <span>Or run the checks yourself</span>
          </div>
          <p className="text-xs text-[var(--ink-muted)] mb-3 leading-relaxed">
            Clone the repository and the two judge-facing suites run locally:
          </p>
          <pre className="font-mono text-xs leading-relaxed overflow-x-auto rounded-xl p-4 bg-black/60 border border-white/5 text-[var(--gold-300)]">
{`make rules-test               # Firestore security-rule assertions
make smoke-safety --gate-only  # the Guardian's decision table
make eval                      # golden fixtures over the whole pipeline`}
          </pre>
        </div>
      </section>

      {/* Non-negotiable on-screen disclosure of how the open event above is configured. */}
      <div className="mb-10">
        <DisclosurePanel />
      </div>

      <section className="flex flex-wrap gap-x-5 gap-y-2 text-xs font-semibold pt-6 border-t border-white/10">
        <a href={REPO} target="_blank" rel="noreferrer" className="flex items-center gap-1 text-[var(--accent)] hover:underline">
          <span>Source code</span>
          <ExternalLink className="w-3.5 h-3.5" />
        </a>
        {VIDEO && (
          <a href={VIDEO} target="_blank" rel="noreferrer" className="flex items-center gap-1 text-[var(--accent)] hover:underline">
            <span>Watch the film</span>
            <ExternalLink className="w-3.5 h-3.5" />
          </a>
        )}
        <a
          href={`${REPO}/blob/main/docs/architecture.md`}
          target="_blank"
          rel="noreferrer"
          className="flex items-center gap-1 text-[var(--accent)] hover:underline"
        >
          <span>Architecture</span>
          <ExternalLink className="w-3.5 h-3.5" />
        </a>
        <a href="/host" className="flex items-center gap-1 text-[var(--accent)] hover:underline">
          <span>Create your own event</span>
          <ArrowRight className="w-3.5 h-3.5" />
        </a>
      </section>

      <footer className="mt-8 pt-6 border-t border-white/5">
        <details className="text-xs text-[var(--ink-muted)]">
          <summary className="hover:text-[var(--ivory)] font-medium cursor-pointer">
            Don&rsquo;t want to wait for the next tick?
          </summary>
          <div className="mt-3 p-4 rounded-2xl glass-card bg-black/40">
            <p className="text-[11px] text-[var(--ink-muted)] mb-3 leading-relaxed">
              This is a manual override — the countdown above is the real cadence, and it fires on
              its own whether or not anyone is watching. The button exists so you don&rsquo;t have to
              sit out a full interval.
            </p>
            <button
              type="button"
              onClick={() => void onForce()}
              disabled={forcing}
              className="btn-secondary px-4 py-2 text-xs font-semibold disabled:opacity-40"
            >
              {forcing ? "Running a tick…" : "Run a tick now"}
            </button>
            {forced && (
              <p className="text-xs text-[var(--accent)] mt-2 font-mono">{forced}</p>
            )}
          </div>
        </details>
      </footer>
    </main>
  );
}

function Step({
  n,
  icon: Icon,
  title,
  href,
  cta,
  children,
}: {
  n: number;
  icon: React.ElementType;
  title: string;
  href?: string;
  cta?: string;
  children: React.ReactNode;
}) {
  return (
    <li className="p-5 rounded-2xl glass-card border border-white/10 hover:border-white/20 transition-all flex gap-4 items-start shadow-md">
      <div className="w-8 h-8 rounded-full bg-[var(--gold-500)]/15 text-[var(--accent)] flex items-center justify-center font-mono text-xs font-bold shrink-0 border border-[var(--gold-500)]/30 mt-0.5">
        {n}
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-1">
          <Icon className="w-4 h-4 text-[var(--accent)] shrink-0" />
          <h3 className="font-[family-name:var(--font-display)] text-lg font-medium text-[var(--ivory)]">
            {title}
          </h3>
        </div>
        <div className="text-xs text-[var(--ink-muted)] leading-relaxed mb-3">{children}</div>
        {href && cta && (
          <a
            href={href}
            target="_blank"
            rel="noreferrer"
            className="btn-primary inline-flex items-center gap-1.5 text-xs font-semibold px-4 py-2"
          >
            <span>{cta}</span>
            <ArrowRight className="w-3.5 h-3.5 stroke-[2.5]" />
          </a>
        )}
      </div>
    </li>
  );
}
