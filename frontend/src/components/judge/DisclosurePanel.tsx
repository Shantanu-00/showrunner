"use client";

import { useState } from "react";

/** Spec 12 §1's design consequence for the architect judge — "demo conveniences disclosed
 * on-screen" — discharged literally.
 *
 * The sorting rule this copy exists to satisfy (HANDOFF §9, S14): a demo convenience is honest if
 * it is a configuration value a real host could also set, and a thumb on the scale if it is a code
 * branch keyed on "this event belongs to a judge." Every difference listed below is the former.
 * The two that were the latter were deleted rather than disclosed — `demoConfig.publicFloor`'s
 * `protected_demo` branch in `shared/visibility.py`, and `autoPromoteEnrollees`, which ships off.
 *
 * That is why the closing sentence can be stated as fact rather than as a promise, and it is why
 * this panel must not drift from the code: if a later session reintroduces a judge-conditional
 * branch, this paragraph becomes the false one.
 *
 * Collapsed by default with the heading visible. Being findable is the point; being loud is not —
 * rules §11's deception discretion is the asymmetry that makes disclosure non-optional, and a
 * disclosed configuration difference is not deception under any reading.
 */
export function DisclosurePanel() {
  const [open, setOpen] = useState(false);

  return (
    <section
      className="rounded-[var(--radius-card)] overflow-hidden"
      style={{ background: "var(--bg-1)", border: "var(--hairline)" }}
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="w-full text-left px-4 py-3 flex items-center justify-between gap-3"
        style={{ minHeight: 44 }}
      >
        <span className="text-sm" style={{ color: "var(--ivory)" }}>
          What&rsquo;s different about this demo event — all of it
        </span>
        <span
          className="font-mono text-xs shrink-0"
          style={{ color: "var(--accent, var(--gold-500))" }}
          aria-hidden
        >
          {open ? "hide" : "read"}
        </span>
      </button>

      {open && (
        <div className="px-4 pb-4 space-y-3 text-sm" style={{ color: "var(--ink-muted)" }}>
          <p>
            This is a real event on the real system. The pipeline, the agents, the consent gates and
            the security rules are identical to a live wedding&rsquo;s. Three settings differ, and
            every one of them is a value a real host can also set:
          </p>

          <ul className="space-y-2 pl-4" style={{ listStyle: "disc" }}>
            <li>
              <strong style={{ color: "var(--ivory)" }}>
                The quality floor for the public wall is 0, not 0.45.
              </strong>{" "}
              Your test photo of your desk should still reach the kiosk. Quality still decides which
              photos get the <em>hero</em> slots — the aesthetic score is a ranking term everywhere,
              it just isn&rsquo;t a gate here.
            </li>
            <li>
              <strong style={{ color: "var(--ivory)" }}>The timeline is compressed</strong> — stage
              windows are minutes long instead of hours, so a two-day wedding fits in a coffee
              break. Nothing special-cases this; the same Event Graph the real temporal logic reads
              is simply configured with shorter windows.
            </li>
            <li>
              <strong style={{ color: "var(--ivory)" }}>
                The director reconciles every 30 seconds instead of every 2 minutes
              </strong>
              , via a second Cloud Scheduler job (<code className="font-mono">* * * * *</code>) plus
              a +30&nbsp;s Cloud Tasks interleave, because Scheduler&rsquo;s cron floor is one
              minute. Real infrastructure, faster clock.
            </li>
          </ul>

          <p>
            This event is also exempt from the platform&rsquo;s concurrent-live-event cap, which is
            enforced transactionally when any other event goes live. That&rsquo;s deliberate and
            it&rsquo;s the point of the cap: it exists so a stranger cannot squat the capacity a judge
            needs.{" "}
            <strong style={{ color: "var(--ivory)" }}>
              A stranger cannot lock you out of this page, whatever they do.
            </strong>
          </p>

          <p>
            Two related limits are <em>designed and configured but not enforced</em>, and it would be
            easy not to mention it: a 60-minute auto-wrap and a $3 per-event cost ceiling for
            stranger-created events both need an hourly sweep that this build does not ship. So rather
            than rely on a guardrail that is not running, public event creation is switched{" "}
            <strong style={{ color: "var(--ivory)" }}>off</strong> for the judging period — one
            Firestore flag, the same admin kill switch the design specifies. This demo event is
            unaffected, and so is everything in the tour above.
          </p>

          <p>
            Nothing else changes. There is no code path in this system whose behaviour depends on
            you being a judge. This event resets nightly through the same upload API a guest&rsquo;s
            phone uses — never a direct database write, so what you inspect is what a guest
            produces.
          </p>
        </div>
      )}
    </section>
  );
}
