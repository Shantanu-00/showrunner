import Link from "next/link";
import {
  Sparkles,
  CalendarPlus,
  QrCode,
  Compass,
  ArrowRight,
  Camera,
  Wand2,
  Tv,
  ExternalLink,
} from "lucide-react";

const REPO = process.env.NEXT_PUBLIC_REPO_URL ?? "https://github.com/Shantanu-00/showrunner";
const VIDEO = process.env.NEXT_PUBLIC_VIDEO_URL ?? "";

// The landing surface. Spec 12 §5.1 originally put the guided walkthrough on `/` with a quiet
// "create your own event" link; that inverted the two audiences — a host arriving cold, and a guest
// who mistyped a QR link, both landed inside a walkthrough written for someone evaluating the
// product. `/` now answers the only question a first-time visitor has ("what is this, and which of
// these three am I?") in one screen; the walkthrough lives at /how-it-works.
//
// Deliberately a server component: no state, no effects, no client bundle — this is the LCP-critical
// route (spec 12 §10) and it should cost nothing to render.
const CHOICES: Array<{
  href: string;
  icon: React.ElementType;
  eyebrow: string;
  title: string;
  body: string;
  cta: string;
  primary?: boolean;
}> = [
  {
    href: "/host",
    icon: CalendarPlus,
    eyebrow: "I'm hosting",
    title: "Create an event",
    body:
      "Name it, pick its look, and get a QR code your guests scan. Takes about a minute, and you don't need an account.",
    cta: "Create an event",
    primary: true,
  },
  {
    href: "/join",
    icon: QrCode,
    eyebrow: "I'm a guest",
    title: "Join an event",
    body:
      "Someone gave you a code, or there's a QR code on the wall. Share photos, and find the ones you're in.",
    cta: "Enter an invite code",
  },
  {
    href: "/how-it-works",
    icon: Compass,
    eyebrow: "Just looking",
    title: "See how it works",
    body:
      "A walkthrough of the whole thing — the guest phone, the big screen, and the host's controls — with a live event you can open.",
    cta: "Take the walkthrough",
  },
];

const BEATS: Array<{ icon: React.ElementType; title: string; body: string }> = [
  {
    icon: Camera,
    title: "Guests shoot",
    body:
      "They scan a QR code and start sending photos in two taps. No app install, no sign-up, no email address.",
  },
  {
    icon: Wand2,
    title: "Agents direct",
    body:
      "A fleet of agents reads every photo — quality, who's in it, whether it's dignified to show — and asks guests for the shots nobody captured yet.",
  },
  {
    icon: Tv,
    title: "The room watches",
    body:
      "The best shots reach the big screen seconds after they're taken, and each guest gets a private album of every photo they appear in.",
  },
];

export default function LandingPage() {
  return (
    <main className="min-h-screen px-5 py-14 mx-auto max-w-3xl">
      <header className="mb-12">
        <div className="flex items-center gap-2 mb-4">
          <span className="p-1.5 rounded-lg bg-[var(--gold-500)]/15 text-[var(--accent)] border border-[var(--gold-500)]/20">
            <Sparkles className="w-4 h-4" />
          </span>
          <span className="text-[11px] font-mono tracking-[0.2em] uppercase font-bold text-[var(--accent)]">
            SHOWRUNNER
          </span>
        </div>
        <h1 className="font-[family-name:var(--font-display)] text-4xl sm:text-5xl font-semibold leading-[1.1] text-gold-gradient mb-5">
          Your event gets its own
          <br />
          media director.
        </h1>
        <p className="text-sm sm:text-base text-[var(--ivory-dim)] leading-relaxed max-w-xl">
          Guests photograph the night on the phones they already have. Showrunner curates as it
          arrives — putting the best moments on the big screen live, sending every guest a private
          album of the photos they&rsquo;re in, and asking the room for the shots nobody thought to
          take.
        </p>
      </header>

      <section className="space-y-3 mb-14" aria-label="Choose how you want to start">
        {CHOICES.map((c) => (
          <Link
            key={c.href}
            href={c.href}
            className={`block p-5 sm:p-6 rounded-3xl glass-card border transition-all group ${
              c.primary
                ? "border-[var(--gold-500)]/40 shadow-xl"
                : "border-white/10 hover:border-white/25"
            }`}
          >
            <div className="flex items-start gap-4">
              <span
                className={`shrink-0 w-11 h-11 rounded-2xl flex items-center justify-center border ${
                  c.primary
                    ? "bg-[var(--gold-500)]/15 border-[var(--gold-500)]/30 text-[var(--accent)]"
                    : "bg-white/5 border-white/10 text-[var(--ivory-dim)]"
                }`}
              >
                <c.icon className="w-5 h-5" />
              </span>
              <div className="flex-1 min-w-0">
                <p className="text-[11px] font-mono uppercase tracking-[0.18em] text-[var(--ink-muted)] mb-1">
                  {c.eyebrow}
                </p>
                <h2 className="font-[family-name:var(--font-display)] text-xl sm:text-2xl font-medium text-[var(--ivory)] mb-1.5">
                  {c.title}
                </h2>
                <p className="text-xs sm:text-sm text-[var(--ink-muted)] leading-relaxed mb-3">
                  {c.body}
                </p>
                <span
                  className={`inline-flex items-center gap-1.5 text-xs font-semibold ${
                    c.primary ? "text-[var(--accent)]" : "text-[var(--ivory-dim)]"
                  }`}
                >
                  <span>{c.cta}</span>
                  <ArrowRight className="w-3.5 h-3.5 stroke-[2.5] transition-transform group-hover:translate-x-0.5" />
                </span>
              </div>
            </div>
          </Link>
        ))}
      </section>

      <section className="mb-14">
        <h2 className="font-[family-name:var(--font-display)] text-2xl font-semibold text-[var(--ivory)] mb-1">
          What actually happens
        </h2>
        <p className="text-xs text-[var(--ink-muted)] mb-6">
          Three things, running the whole time, with nobody driving them.
        </p>
        <ol className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          {BEATS.map((b, i) => (
            <li
              key={b.title}
              className="p-5 rounded-2xl glass-card border border-white/10 flex flex-col gap-2"
            >
              <div className="flex items-center gap-2">
                <b.icon className="w-4 h-4 text-[var(--accent)]" />
                <span className="text-[11px] font-mono text-[var(--ink-faint)] tabular-nums">
                  0{i + 1}
                </span>
              </div>
              <h3 className="font-[family-name:var(--font-display)] text-lg font-medium text-[var(--ivory)]">
                {b.title}
              </h3>
              <p className="text-xs text-[var(--ink-muted)] leading-relaxed">{b.body}</p>
            </li>
          ))}
        </ol>
      </section>

      <section className="p-5 rounded-2xl border border-white/10 bg-white/[0.02] mb-10">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-[var(--ivory)] mb-2">
          Where the lines are drawn
        </h2>
        <ul className="text-xs text-[var(--ink-muted)] leading-relaxed space-y-1.5">
          <li>
            Nothing reaches the big screen unless the person who took it chose to share it there.
          </li>
          <li>
            Face recognition is opt-in per guest, stays inside the one event, and is deleted with it.
          </li>
          <li>
            Anyone who appears in a photo can pull themselves off the public wall in a single tap.
          </li>
        </ul>
      </section>

      <footer className="flex flex-wrap gap-x-5 gap-y-2 text-xs font-semibold pt-6 border-t border-white/10">
        <a
          href={REPO}
          target="_blank"
          rel="noreferrer"
          className="flex items-center gap-1 text-[var(--accent)] hover:underline"
        >
          <span>Source code</span>
          <ExternalLink className="w-3.5 h-3.5" />
        </a>
        <a
          href={`${REPO}/blob/main/docs/architecture.md`}
          target="_blank"
          rel="noreferrer"
          className="flex items-center gap-1 text-[var(--accent)] hover:underline"
        >
          <span>Architecture</span>
          <ExternalLink className="w-3.5 h-3.5" />
        </a>
        {VIDEO && (
          <a
            href={VIDEO}
            target="_blank"
            rel="noreferrer"
            className="flex items-center gap-1 text-[var(--accent)] hover:underline"
          >
            <span>Watch the film</span>
            <ExternalLink className="w-3.5 h-3.5" />
          </a>
        )}
      </footer>
    </main>
  );
}
