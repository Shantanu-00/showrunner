import Link from "next/link";
import Image from "next/image";
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
  ShieldCheck,
  Zap,
} from "lucide-react";

const REPO = process.env.NEXT_PUBLIC_REPO_URL ?? "https://github.com/Shantanu-00/showrunner";
const VIDEO = process.env.NEXT_PUBLIC_VIDEO_URL ?? "";

// The landing surface.
// Answers the visitor's core question ("what is this, and which of these three am I?")
// above the fold in a single responsive screen without awkward scrolling on PC or mobile.
const CHOICES: Array<{
  href: string;
  icon: React.ElementType;
  tag: string;
  eyebrow: string;
  title: string;
  body: string;
  cta: string;
  primary?: boolean;
}> = [
  {
    href: "/host",
    icon: CalendarPlus,
    tag: "Host",
    eyebrow: "I'm hosting",
    title: "Create an event",
    body: "Name it, choose a style, and get a guest QR code in 60 seconds without an account.",
    cta: "Create an event",
    primary: true,
  },
  {
    href: "/join",
    icon: QrCode,
    tag: "Guest",
    eyebrow: "I'm a guest",
    title: "Join an event",
    body: "Enter an invite code or scan a room QR code. Upload photos and find your moments.",
    cta: "Enter invite code",
  },
  {
    href: "/how-it-works",
    icon: Compass,
    tag: "Demo",
    eyebrow: "Live tour",
    title: "See how it works",
    body: "Explore the live director in action — the guest phone, big screen wall, and host AI controls.",
    cta: "Take the walkthrough",
  },
];

const BEATS: Array<{ icon: React.ElementType; title: string; body: string }> = [
  {
    icon: Camera,
    title: "Guests shoot instantly",
    body: "Scan a QR code and send moments in two taps. Zero app installs, zero friction.",
  },
  {
    icon: Wand2,
    title: "AI agents direct live",
    body: "Multimodal models index faces, assess aesthetics, and post missions for missing angles.",
  },
  {
    icon: Tv,
    title: "Big screen & private albums",
    body: "Curated highlights beam to the projector in seconds, while guests get private albums of themselves.",
  },
];

export default function LandingPage() {
  return (
    <main className="min-h-screen px-4 sm:px-6 py-6 sm:py-10 mx-auto max-w-5xl flex flex-col justify-between">
      <div>
        {/* Compact Hero Section */}
        <header className="mb-6 sm:mb-8 text-center sm:text-left">
          <div className="inline-flex items-center gap-2.5 px-3 py-1.5 rounded-full glass-pill bg-white/5 border border-white/10 shadow-sm mb-3">
            <Image
              src="/logo.png"
              alt="Showrunner Clapperboard"
              width={24}
              height={24}
              className="w-4 h-4 sm:w-5 sm:h-5 object-cover rounded shadow-[0_0_8px_rgba(234,179,8,0.25)] border border-amber-400/30 shrink-0"
              priority
            />
            <span className="text-[10px] sm:text-[11px] font-mono tracking-[0.2em] uppercase font-bold text-[var(--accent)]">
              SHOWRUNNER AI MEDIA DIRECTOR
            </span>
          </div>

          <h1 className="font-[family-name:var(--font-display)] text-3xl sm:text-4xl md:text-5xl font-bold leading-[1.12] text-gold-gradient mb-3">
            Your event gets its own
            <br className="hidden sm:inline" />
            {" "}media director.
          </h1>

          <p className="text-xs sm:text-sm md:text-base text-[var(--ivory-dim)] leading-relaxed max-w-2xl">
            Guests capture moments on their phones. Showrunner’s autonomous agent fleet curates in real time — streaming highlights to the big screen, creating private face albums, and directing the room for missed shots.
          </p>
        </header>

        {/* 3 Core Action Cards — 3-Col Grid on Desktop, Compact High-Density Deck on Mobile */}
        <section
          className="grid grid-cols-1 md:grid-cols-3 gap-3.5 sm:gap-4 mb-10 sm:mb-12"
          aria-label="Choose how you want to start"
        >
          {CHOICES.map((c) => (
            <Link
              key={c.href}
              href={c.href}
              className={`group relative flex flex-col justify-between p-4 sm:p-5 rounded-2xl glass-card border transition-all duration-300 transform hover:-translate-y-1 active:scale-[0.98] ${
                c.primary
                  ? "border-[var(--accent)]/50 bg-gradient-to-b from-[var(--accent)]/10 via-[var(--bg-glass)] to-[var(--bg-glass)] shadow-[0_8px_30px_-6px_var(--accent-glow)]"
                  : "border-white/10 hover:border-white/25 hover:bg-white/[0.04]"
              }`}
            >
              <div>
                {/* Card Top Row: Icon & Tag */}
                <div className="flex items-center justify-between mb-3">
                  <span
                    className={`w-10 h-10 rounded-xl flex items-center justify-center border transition-transform duration-300 group-hover:scale-105 ${
                      c.primary
                        ? "bg-[var(--accent)]/20 border-[var(--accent)]/40 text-[var(--accent)] shadow-md"
                        : "bg-white/5 border-white/10 text-[var(--ivory-dim)] group-hover:text-white"
                    }`}
                  >
                    <c.icon className="w-5 h-5" />
                  </span>

                  <span
                    className={`text-[10px] font-mono uppercase tracking-wider font-bold px-2 py-0.5 rounded-full border ${
                      c.primary
                        ? "bg-[var(--accent)]/15 border-[var(--accent)]/30 text-[var(--accent)]"
                        : "bg-white/5 border-white/10 text-[var(--ink-muted)]"
                    }`}
                  >
                    {c.tag}
                  </span>
                </div>

                <p className="text-[10px] font-mono uppercase tracking-[0.16em] text-[var(--ink-muted)] mb-1">
                  {c.eyebrow}
                </p>

                <h2 className="font-[family-name:var(--font-display)] text-lg sm:text-xl font-semibold text-[var(--ivory)] mb-1.5 group-hover:text-[var(--accent)] transition-colors">
                  {c.title}
                </h2>

                <p className="text-xs text-[var(--ink-muted)] leading-relaxed mb-4">
                  {c.body}
                </p>
              </div>

              {/* Card Action Link */}
              <div className="pt-2 border-t border-white/5 flex items-center justify-between">
                <span
                  className={`inline-flex items-center gap-1.5 text-xs font-semibold ${
                    c.primary ? "text-[var(--accent)]" : "text-[var(--ivory-dim)] group-hover:text-white"
                  }`}
                >
                  <span>{c.cta}</span>
                  <ArrowRight className="w-3.5 h-3.5 stroke-[2.5] transition-transform group-hover:translate-x-1" />
                </span>
                {c.primary && (
                  <span className="w-1.5 h-1.5 rounded-full bg-[var(--accent)] animate-ping" />
                )}
              </div>
            </Link>
          ))}
        </section>

        {/* What Actually Happens — 3 Core Pillars */}
        <section className="mb-10 sm:mb-12">
          <div className="flex items-center gap-2 mb-1">
            <Zap className="w-3.5 h-3.5 text-[var(--accent)]" />
            <h2 className="font-[family-name:var(--font-display)] text-lg sm:text-xl font-semibold text-[var(--ivory)]">
              Autonomous Real-Time Directing
            </h2>
          </div>
          <p className="text-xs text-[var(--ink-muted)] mb-4">
            Three intelligent agents running continuously in the background.
          </p>

          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            {BEATS.map((b, i) => (
              <div
                key={b.title}
                className="p-4 rounded-xl glass-card border border-white/10 flex flex-col gap-2 hover:border-white/20 transition-all"
              >
                <div className="flex items-center justify-between">
                  <b.icon className="w-4 h-4 text-[var(--accent)]" />
                  <span className="text-[10px] font-mono text-[var(--ink-faint)] tabular-nums font-bold">
                    0{i + 1}
                  </span>
                </div>
                <h3 className="font-[family-name:var(--font-display)] text-sm sm:text-base font-medium text-[var(--ivory)]">
                  {b.title}
                </h3>
                <p className="text-xs text-[var(--ink-muted)] leading-relaxed">{b.body}</p>
              </div>
            ))}
          </div>
        </section>

        {/* Privacy & Guardrails */}
        <section className="p-4 sm:p-5 rounded-2xl border border-white/10 bg-white/[0.02] mb-8">
          <div className="flex items-center gap-2 mb-2">
            <ShieldCheck className="w-4 h-4 text-emerald-400" />
            <h2 className="text-xs font-semibold uppercase tracking-wider text-[var(--ivory)]">
              Strict Privacy & Dignity Guardrails
            </h2>
          </div>
          <ul className="text-xs text-[var(--ink-muted)] leading-relaxed space-y-1.5 list-disc list-inside">
            <li>
              Guests maintain full consent: Photos only reach public screens with explicit permission.
            </li>
            <li>
              Opt-in facial indexing: Ephemeral biometric vectors stay strictly isolated to the event.
            </li>
            <li>
              Instant revocation: Guests can pull any moment off the public wall in a single tap.
            </li>
          </ul>
        </section>
      </div>

      {/* Footer */}
      <footer className="flex flex-wrap items-center justify-between gap-3 text-xs font-medium pt-4 border-t border-white/10 text-[var(--ink-muted)]">
        <div className="flex items-center gap-4">
          <a
            href={REPO}
            target="_blank"
            rel="noreferrer"
            className="flex items-center gap-1 text-[var(--accent)] hover:underline"
          >
            <span>Source Code</span>
            <ExternalLink className="w-3 h-3" />
          </a>
          <a
            href={`${REPO}/blob/main/docs/architecture.md`}
            target="_blank"
            rel="noreferrer"
            className="flex items-center gap-1 text-[var(--accent)] hover:underline"
          >
            <span>Architecture</span>
            <ExternalLink className="w-3 h-3" />
          </a>
          {VIDEO && (
            <a
              href={VIDEO}
              target="_blank"
              rel="noreferrer"
              className="flex items-center gap-1 text-[var(--accent)] hover:underline"
            >
              <span>Watch Film</span>
              <ExternalLink className="w-3 h-3" />
            </a>
          )}
        </div>

        <div className="inline-flex items-center gap-2 text-[11px] font-mono text-[var(--ink-faint)]">
          <Image
            src="/logo.png"
            alt="Showrunner Logo"
            width={16}
            height={16}
            className="w-3.5 h-3.5 sm:w-4 sm:h-4 object-cover rounded shadow-sm border border-amber-400/30 shrink-0"
          />
          <span>Showrunner · Autonomous Event Director</span>
        </div>
      </footer>
    </main>
  );
}

