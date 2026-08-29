"use client";

import { useCallback, useEffect, useState } from "react";
import { ensureAnonymousAuth } from "@/lib/firebase";
import { getEventPublic, warmup, forceDemoTick } from "@/lib/api";
import type { EventPublicInfo } from "@/lib/types";
import { TickCountdownView } from "@/components/host/TickCountdown";
import { DisclosurePanel } from "./DisclosurePanel";

const EVENT_ID = process.env.NEXT_PUBLIC_JUDGE_EVENT_ID ?? "judge_demo";
const PROJECT = process.env.NEXT_PUBLIC_GCP_PROJECT ?? "showrunner-hq";
const REPO = process.env.NEXT_PUBLIC_REPO_URL ?? "https://github.com/Shantanu-00/showrunner";
const VIDEO = process.env.NEXT_PUBLIC_VIDEO_URL ?? "";

const CONSOLE_LINKS: Array<{ label: string; note: string; href: string }> = [
  {
    label: "Cloud Run — seven services",
    note: "us-central1, revision tags, all green",
    href: `https://console.cloud.google.com/run?project=${PROJECT}`,
  },
  {
    label: "Cloud Scheduler — director-tick",
    note: "the autonomy claim's evidence surface: Last run · Success",
    href: `https://console.cloud.google.com/cloudscheduler?project=${PROJECT}`,
  },
  {
    label: "Logs Explorer — one-line stage logs",
    note: "stage=curate media=… ms=1180 tokens_in=1548 verdict=highlight",
    href: `https://console.cloud.google.com/logs/query?project=${PROJECT}`,
  },
  {
    label: "act.py — every number that decides who gets paid",
    note: "one file, pure functions, checkable with no cloud account",
    href: `${REPO}/blob/main/backend/directors/story/act.py`,
  },
];

/** Spec 09 §4's judge-mode landing, and the answer to rules §4's own trapdoor — *"judges are not
 * required to test the Project and may choose to judge based solely on the text description, images,
 * and video."* An unguided URL is an opt-out; this page is the guided path that makes the working
 * system count.
 *
 * Two disciplines it must keep:
 * - **The countdown, not a button, is the autonomy beat** (EXECUTION-PLAN §7e row 11). A judge
 *   pressing "Run director now" seconds before reading a claim of *"without human intervention"* is
 *   a rules-§4 "must function as depicted" contradiction. The manual trigger survives below the
 *   fold, labelled as an override, because a 15-minute judging-month cadence needs an escape hatch
 *   for dead air — not because it is the demo.
 * - **Every difference from a real event is disclosed on this page** (`DisclosurePanel`), which is
 *   what makes the whole surface safe to ship rather than something to defend.
 */
export function JudgeTour() {
  const [info, setInfo] = useState<EventPublicInfo | null>(null);
  const [forcing, setForcing] = useState(false);
  const [forced, setForced] = useState<string | null>(null);

  const load = useCallback(() => {
    void getEventPublic(EVENT_ID).then(setInfo, () => {});
  }, []);

  useEffect(() => {
    // Anonymous auth first: `getEventPublic` is an authenticated call, and signing in *is* joining
    // (spec 02 §1) — there is no account step between a judge and the product, same as a guest.
    void ensureAnonymousAuth().then(() => {
      load();
      // Fire-and-forget. `worker-face` holds a 326 MB InsightFace model and cold-starts in ~30 s;
      // by the time a judge finishes reading and taps upload, the hot path is warm. Never awaited,
      // never blocks, and a failure here is invisible by design.
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
      setForced(res.ran ? "Tick ran — watch the phone." : (res.message ?? "Already ticking; hold on."));
      load();
    } catch {
      setForced("Couldn't force one — the scheduled tick above still runs regardless.");
    } finally {
      setForcing(false);
    }
  }

  const director = info?.director;
  const joinBase = `/join/${EVENT_ID}?judge=1`;

  return (
    <main className="min-h-screen px-5 py-10 mx-auto" style={{ background: "var(--bg-0)", maxWidth: 720 }}>
      <header>
        <p
          className="text-[11px] tracking-[0.18em] uppercase mb-2"
          style={{ color: "var(--accent, var(--gold-500))" }}
        >
          The Taskmaster · All Things Agentic
        </p>
        <h1 className="font-[var(--font-display)] text-4xl leading-tight" style={{ color: "var(--ivory)" }}>
          Judging Showrunner?
          <br />
          Here&rsquo;s the 60-second loop.
        </h1>
        <p className="text-sm mt-4" style={{ color: "var(--ink-muted)" }}>
          An autonomous event media director. Guests upload from their phones; an agent fleet
          classifies, face-indexes and safety-screens every photo, runs a live kiosk and private
          per-person albums, then <strong style={{ color: "var(--ivory)" }}>tasks the crowd</strong>{" "}
          with photo bounties to fill the gaps it finds. No chat window exists anywhere in this
          product.
        </p>
        {info?.exists === false && (
          <p className="text-sm mt-4 rounded-[var(--radius-card)] p-3" style={{ background: "var(--bg-1)", color: "var(--warn)" }}>
            The demo event isn&rsquo;t seeded right now. Everything below still links correctly —
            please email the address in the submission instructions and I&rsquo;ll reseed within the hour.
          </p>
        )}
      </header>

      <div className="mt-8 grid gap-3" style={{ gridTemplateColumns: "1fr 1fr" }}>
        <TickCountdownView
          lastTickAtMs={director?.lastTickAt ? Date.parse(director.lastTickAt) : null}
          tickCount={director?.tickCount ?? 0}
          cadenceSec={director?.cadenceSec ?? 120}
          onDue={load}
        />
        <div
          className="rounded-[var(--radius-card)] p-3"
          style={{ background: "var(--bg-1)", border: "var(--hairline)" }}
        >
          <p className="text-xs mb-1" style={{ color: "var(--ink-muted)" }}>
            Event status
          </p>
          <p className="font-mono text-xl" style={{ color: "var(--ivory)" }}>
            {info?.status ?? "…"}
          </p>
          <p className="text-[10px] mt-1" style={{ color: "var(--ink-muted)" }}>
            {info?.activeStage ? `now: ${info.activeStage}` : "seeded, live, self-tending"}
          </p>
        </div>
      </div>

      <div className="mt-4">
        <DisclosurePanel />
      </div>

      <ol className="mt-8 space-y-5">
        <Step n={1} title="Watch the wall" href={`/kiosk/${EVENT_ID}`} cta="Open the kiosk ↗">
          A directed show, not a gallery on a TV — ranked by a deterministic score the publisher
          stores on every slot, so the &ldquo;why this photo?&rdquo; card can never disagree with the
          decision. Tap <em>Start show</em> once (browsers need a gesture before fullscreen and audio).
        </Step>

        <Step n={2} title="Join as a guest" href={joinBase} cta="Join the event ↗">
          One tap. No email, no password, no account — anonymous sign-in <em>is</em> the act of
          joining, exactly as it is for a wedding guest who scanned a QR code.
        </Step>

        <Step n={3} title="Send a photo" href={`${joinBase}&samples=1`} cta="Upload 3 samples ↗">
          Three photos ship with this page, so there&rsquo;s no file picker and no camera permission.
          <strong style={{ color: "var(--ivory)" }}>
            {" "}
            When the send sheet appears, tap &ldquo;Share to the big screen&rdquo;
          </strong>{" "}
          — everything here is private by default, so without that step your upload correctly stays
          out of the kiosk. Then watch the chips: the Curator is judging your shot → looking for you
          in the archives → the Guardian&rsquo;s last look → <strong style={{ color: "var(--ok)" }}>live</strong>.
          About six seconds warm.
        </Step>

        <Step n={4} title="Now watch it work with nobody touching it">
          The countdown above is a real Cloud Scheduler cron. When it reaches zero the Story Director
          reads its coverage ledger, finds a gap, and a{" "}
            <strong style={{ color: "var(--ivory)" }}>bounty banner lands on the guest phone</strong>{" "}
          — a photograph nobody asked for, requested by an agent. This is the 40% criterion, and
          there is no button in this step.
        </Step>

        <Step n={5} title="Answer the director" href={joinBase} cta="Back to the phone ↗">
          Tap <em>Shoot now</em> on the banner and send another sample. Identity in the award is
          deterministic — a 512-d ArcFace match, never a language model. The model is asked exactly
          one question, from <em>text only</em> (the caption, not the photograph), so the service
          running the planner has no grant to read a guest&rsquo;s picture at all. Points land within
          one tick; the photo hits the wall immediately.
        </Step>

        <Step n={6} title="Look inside — the host console">
          Credentials are in the Devpost submission instructions (deliberately not printed on a
          public page — a host link can freeze or wrap this event). You get the real state machine,
          real aggregates, stage overrides, the wrap report, and a{" "}
          <strong style={{ color: "var(--ivory)" }}>Freeze public</strong> shield. Please press it:
          every public surface empties in under five seconds, then unfreeze. A consent control that
          visibly works beats any paragraph about consent architecture.
        </Step>

        <Step n={7} title="The receipts">
          <div className="mt-2 space-y-2">
            {CONSOLE_LINKS.map((l) => (
              <a
                key={l.href}
                href={l.href}
                target="_blank"
                rel="noreferrer"
                className="block rounded-[var(--radius-card)] px-3 py-2"
                style={{ background: "var(--bg-0)", border: "var(--hairline)" }}
              >
                <span className="text-sm block" style={{ color: "var(--ivory)" }}>
                  {l.label} ↗
                </span>
                <span className="text-[11px]" style={{ color: "var(--ink-muted)" }}>
                  {l.note}
                </span>
              </a>
            ))}
          </div>
          <p className="text-[11px] mt-3" style={{ color: "var(--ink-muted)" }}>
            Console links need your own Google account to view — they prove the deployment exists,
            they aren&rsquo;t part of the 60-second loop.
          </p>
        </Step>
      </ol>

      <section
        className="mt-8 rounded-[var(--radius-card)] p-4"
        style={{ background: "var(--bg-1)", border: "var(--hairline)" }}
      >
        <h2 className="text-sm mb-2" style={{ color: "var(--ivory)" }}>
          Prefer to verify without touching my cloud project?
        </h2>
        <p className="text-[13px] mb-3" style={{ color: "var(--ink-muted)" }}>
          Three checks that need no GCP account and no credentials — clone the repo and run them:
        </p>
        <pre
          className="font-mono text-[11px] leading-relaxed overflow-x-auto rounded-[var(--radius-card)] p-3"
          style={{ background: "var(--bg-0)", color: "var(--ink-muted)" }}
        >
{`make rules-test              # 67 Firestore-rules assertions (emulator)
make smoke-safety --gate-only # the safety gate as a 15-row decision table
make eval                     # 25 golden fixtures, 144 checks`}
        </pre>
      </section>

      <section className="mt-8 flex flex-wrap gap-3 text-sm">
        <a href={REPO} target="_blank" rel="noreferrer" style={{ color: "var(--accent, var(--gold-500))" }}>
          Repository ↗
        </a>
        {VIDEO && (
          <a href={VIDEO} target="_blank" rel="noreferrer" style={{ color: "var(--accent, var(--gold-500))" }}>
            4-minute demo video ↗
          </a>
        )}
        <a
          href={`${REPO}/blob/main/docs/architecture.md`}
          target="_blank"
          rel="noreferrer"
          style={{ color: "var(--accent, var(--gold-500))" }}
        >
          Architecture ↗
        </a>
      </section>

      <footer className="mt-10 pt-6" style={{ borderTop: "var(--hairline)" }}>
        <details>
          <summary className="text-xs cursor-pointer" style={{ color: "var(--ink-muted)" }}>
            Impatient?
          </summary>
          <div className="mt-3">
            <button
              type="button"
              onClick={() => void onForce()}
              disabled={forcing}
              className="rounded-[var(--radius-pill)] px-4 py-2 text-sm"
              style={{
                background: "transparent",
                border: "var(--hairline)",
                color: "var(--ink-muted)",
                minHeight: 44,
                opacity: forcing ? 0.5 : 1,
              }}
            >
              {forcing ? "forcing a tick…" : "Force a tick now"}
            </button>
            <p className="text-[11px] mt-2" style={{ color: "var(--ink-muted)" }}>
              This is a <strong>manual override</strong> — the countdown above is the real cadence,
              and it runs whether or not anyone visits this page. It exists so a judge doesn&rsquo;t
              have to wait out a full interval; the autonomy claim rests on the schedule, not on this
              button.
            </p>
            {forced && (
              <p className="text-[11px] mt-2" style={{ color: "var(--accent, var(--gold-500))" }}>
                {forced}
              </p>
            )}
          </div>
        </details>

        <p className="text-[11px] mt-6" style={{ color: "var(--ink-muted)" }}>
          Running scaled down for the judging period, so the first action may take a few extra
          seconds. The demo event resets nightly through the same upload API a guest&rsquo;s phone
          uses. Built solo for the All Things Agentic Hackathon.
        </p>
      </footer>
    </main>
  );
}

function Step({
  n,
  title,
  href,
  cta,
  children,
}: {
  n: number;
  title: string;
  href?: string;
  cta?: string;
  children: React.ReactNode;
}) {
  return (
    <li className="flex gap-4">
      <span
        className="font-mono text-sm shrink-0 rounded-[var(--radius-pill)] flex items-center justify-center"
        style={{
          width: 28,
          height: 28,
          border: "var(--hairline)",
          color: "var(--accent, var(--gold-500))",
        }}
        aria-hidden
      >
        {n}
      </span>
      <div className="flex-1">
        <h2 className="font-[var(--font-display)] text-lg" style={{ color: "var(--ivory)" }}>
          {title}
        </h2>
        <div className="text-[13px] mt-1 leading-relaxed" style={{ color: "var(--ink-muted)" }}>
          {children}
        </div>
        {href && cta && (
          <a
            href={href}
            target="_blank"
            rel="noreferrer"
            className="inline-block mt-2 rounded-[var(--radius-pill)] px-4 py-2 text-sm"
            style={{
              background: "var(--accent, var(--gold-500))",
              color: "var(--bg-0)",
              minHeight: 44,
            }}
          >
            {cta}
          </a>
        )}
      </div>
    </li>
  );
}
