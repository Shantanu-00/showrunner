"""DIRECT — spec 06 §3 step 2: the one creative model call in the system.

Spec 06 §2's claim is that this will not be generic, and the claim has to be paid for in this file:

1. **Evidence in, narrative out.** The prompt is the candidate list as *structured evidence* — the
   Curator's captions and moment tags, who is in frame with their tier, the capture times, which
   photograph closed a bounty. Step one of the answer is a narrative brief about what actually
   happened. Two events produce different briefs because the evidence differs, and two personas at
   one event produce different briefs because the evidence they are shown differs (`select.py`).
2. **Persona lens.** Same machinery, four editorial mandates. The mandate changes selection and
   pacing, never a colour filter.
3. **Structural freedom the model actually exercises:** shot count within the 10–24 band, one of
   three pacing curves, three transitions from the palette, caption voice, and the music brief that
   becomes the Lyria prompt.
4. **A style seed.** `hash(eventId, persona, version)` is printed into the prompt and used as the
   temperature's tie-breaker, so a re-run of the same commission differs (spec 06 §2.3) — which is
   the difference between "the reel was re-edited" and "the reel was re-rendered".

Two constraints that are *not* creative, stated as hard rules because they have consequences the
model cannot see:

- **Only mediaIds from the evidence.** Same discipline as the Story Director: `critic.py::lint`
  re-derives the manifest from the selected candidate set and rejects anything else, so the prompt's
  job is to make the right answer easy rather than to be the enforcement.
- **Latin script only** (spec 06 §3 step 5, decided in HANDOFF §3). ffmpeg cannot shape Devanagari;
  libass + Noto is the documented production path and is deliberately out of scope. So "Haldi vibes"
  is in and the same words in Devanagari are out, and the linter drops any caption that is not.
"""

from __future__ import annotations

import functools
import hashlib

from google.adk.agents import LlmAgent
from google.genai import types

from schemas.reel import ReelPersona, ReelPlan, Transition
from services.gemini import adk_model, as_text_part
from shared.settings import REEL_MAX_SHOTS, REEL_MIN_SHOTS, REEL_SHOT_REQUEST_MIN, settings

from .select import Candidate

#: Spec 06 §2.2's four mandates, one paragraph each. This is the persona *lens* — the only thing that
#: differs between two commissions over the same event, besides the evidence itself.
PERSONA_LENS: dict[ReelPersona, str] = {
    ReelPersona.COUPLE: (
        "COUPLE — an intimacy arc. You are cutting the film the two of them will watch alone at 2am. "
        "Look for glances, hands, the half-second before a laugh, someone watching someone else who "
        "is not looking back. Crowd shots are context, not content; use them to breathe between close "
        "moments. Slow down at the emotional anchor rather than cutting away from it. Captions are "
        "sparse and never explain the picture."
    ),
    ReelPersona.STAGE_RECAP: (
        "STAGE_RECAP — ritual structure. This is what happened during one part of the day, in order, "
        "so someone who missed it understands it. Open on the setting, move through the sequence of "
        "the ritual as the evidence shows it, close on the reaction it produced. Captions may name "
        "what is happening, because orientation is the point."
    ),
    ReelPersona.GUEST_ENERGY: (
        "GUEST_ENERGY — a kinetic montage. Dance, laughter, crowds, motion blur, the people who came. "
        "Short shots, hard transitions, no lingering. Nobody is the protagonist; the room is. Captions "
        "are shouts, not sentences, and there should be very few of them."
    ),
    ReelPersona.MAIN_CHARACTER: (
        "MAIN_CHARACTER — one person's day, in order. Every shot must have them in it or be the thing "
        "they were looking at. Chronology is the spine; the arc is arriving, being in it, and the "
        "quiet frame at the end. Captions speak about them in the second person, warmly, rarely."
    ),
    ReelPersona.EVENT_RECAP: (
        "EVENT_RECAP — the whole arc, chronological, one beat per chapter. This is the film the "
        "group watches afterwards and says 'that was us'. The evidence spans the entire event — "
        "days, if it ran that long — so let the stages be the chapters: begin where it began, give "
        "each chapter its single strongest beat, and end on the moment that felt like an ending. "
        "Never spend two shots where one carries the chapter; breadth is this film's depth. A "
        "caption may name a chapter ('Day 3 — Kyoto') and then stay out of the way."
    ),
}

_TRANSITIONS = ", ".join(t.value for t in Transition)

INSTRUCTION = f"""\
You are the Reel Director of a live event: an editor with a cutting room, not an assistant. Nobody is
talking to you. You have been handed every photograph that is eligible for this film, described, and
you decide what film it is. Return one JSON object per the schema.

Work in this order and do not skip the first step.

1. NARRATIVE BRIEF — three to five sentences about what actually happened, from the evidence only.
Name the specific things you can see in the captions and moment tags. Say where the emotional anchor
is. If the evidence is thin, say that instead of inventing a wedding you were not shown. This brief
is what makes the cut specific; a brief that would fit any event will produce a film that fits any
event.

2. TITLE — under 40 characters, from the brief, no colon-subtitle construction.

3. PACING — linear_build (steady acceleration to a peak), peak_and_settle (open big, then land quiet),
or two_act (a break in the middle and a different energy after it). Choose from the shape of the
evidence, not from habit.

4. SHOTS — between {REEL_SHOT_REQUEST_MIN} and {REEL_MAX_SHOTS}, in the order they will play. Ask
for more than you think you need: near-duplicate shots are dropped before the cut is built, so a
plan that only just clears the minimum will not survive. Each shot:
- mediaId: copy one exactly from the evidence. A mediaId you did not read there is rejected and the
  shot is lost. Never use the same photograph twice.
- durationBeats: 2 to 8. Long on the moments that matter, short in a montage run.
- move: push_in, pull_out, pan_left, pan_right, hold. Match the content — push_in on a face you want
  to arrive at, pull_out to reveal a room, hold when the photograph is already doing the work. Do not
  alternate mechanically; three pushes in a row is a build, and four is a tic.
- transition: one of {_TRANSITIONS}. Use exactly three distinct transitions across the whole reel plus
  `cut`, and let the energy pick them: cut and dissolve carry almost everything, fadeblack is a
  chapter break, the wipes and slides belong to kinetic passages.
- captionLine: optional and usually absent. Under 34 characters. ENGLISH OR HINGLISH IN LATIN SCRIPT
  ONLY — "Haldi vibes", "Day 3 — Kyoto", "Baraat has arrived" — never Devanagari or any non-Latin
  script, because the
  renderer cannot shape it and the caption will be dropped. Caption at most a third of the shots.
- emphasis: true for the two or three shots that are the film's beats. They land on downbeats.

5. MUSIC — the brief for the score, which is generated from your words and then beat-matched, so the
tempo you ask for is the tempo the cuts will land on. style and arc in a phrase each, tempoBpm 50-180,
instruments as a short list, culturalRefs only from the cultural elements in the evidence. Ask for an
instrumental; a vocal will fight the captions.

Constraints that are not stylistic: never name a person the evidence did not name, never claim a
ritual the evidence did not show, and never write a caption that describes what the viewer can
already see.
"""


@functools.lru_cache(maxsize=1)
def direct_agent() -> LlmAgent:
    """`gemini-3.7-flash` — spec 06 §3 step 2's model, and the one call in the system asked for taste.

    `temperature=0.95`. The perception agents run at zero because an absolute threshold depends on
    their score being reproducible; the Story Director runs at 0.4 because it writes one guest-facing
    sentence around a decision that must stay stable. This one is choosing a story out of forty
    photographs, and a deterministic editor makes the same film every time — which is the definition
    of the template spec 06 §2 exists to avoid. The linter, not the temperature, is what keeps the
    output valid.
    """
    return LlmAgent(
        name="reel_director",
        description="Turns a described set of photographs into a narrative brief, a storyboard and a music brief.",
        model=adk_model(settings().model_director),
        instruction=INSTRUCTION,
        output_schema=ReelPlan,
        output_key="plan",
        generate_content_config=types.GenerateContentConfig(
            temperature=0.95,
            top_p=0.95,
            # 24 shots × ~7 filled fields, plus a 5-sentence brief and the music block. Measured at
            # ~1.1k output tokens for a 15-shot plan; 8192 leaves room for the maximum plan plus the
            # critic's revision, and a truncated plan surfaces as a *validation* error rather than
            # anything readable (the lesson `directors/story/agent.py` records).
            max_output_tokens=8192,
        ),
    )


def style_seed(event_id: str, persona: ReelPersona, version: int) -> int:
    """Spec 06 §2.3's `hash(eventId, persona, version)`, stable across processes.

    Python's `hash()` is salted per process, so it cannot be used: the same commission re-run after a
    redeploy would get a different seed and the reel document's recorded seed would stop explaining
    the reel. Blake2b, 4 bytes, is enough to vary a tie-break.
    """
    digest = hashlib.blake2b(
        f"{event_id}|{persona.value}|{version}".encode("utf-8"), digest_size=4
    ).digest()
    return int.from_bytes(digest, "big")


def _candidate_line(c: Candidate, names: dict[str, str]) -> str:
    who = ", ".join(names.get(p, p) for p in c.person_ids) or "unidentified"
    when = c.captured_at.strftime("%H:%M") if c.captured_at is not None else "--:--"
    bits = [
        f"- {c.media_id} [{when}] stage={c.stage_id or 'unknown'} aesthetic={c.aesthetic:.2f}",
        f"  who={who} facesInFrame={len(c.face_boxes)} peopleInShot={c.people_count or '?'}",
        f"  caption={c.caption or '(none)'!r}",
    ]
    if c.moment_tags:
        bits.append(f"  moments={', '.join(c.moment_tags)}")
    if c.cultural_elements:
        bits.append(f"  culturalElements={', '.join(c.cultural_elements)}")
    if c.bounty_id:
        # Bounty provenance is evidence about *intent*: someone was asked for this photograph and went
        # and took it, which usually means it is the moment rather than a moment (spec 06 §2.1).
        bits.append("  provenance=shot in response to a bounty the director issued")
    return "\n".join(bits)


def evidence_block(
    *,
    event: dict[str, object],
    persona: ReelPersona,
    candidates: list[Candidate],
    names: dict[str, str],
    seed: int,
    stage_id: str | None = None,
    person_id: str | None = None,
    critique: list[str] | None = None,
    previous_shot_ids: list[str] | None = None,
    venue: str = "",
    diary: dict[str, str] | None = None,
) -> str:
    """The whole of what the director is told. Evidence, the lens, and nothing else.

    `venue` is the world model's distilled prose (`directors/story/world.py::recall_prose`) — one
    document read the caller already did, never a model call inside this one. It answers the mandate
    `PERSONA_LENS[STAGE_RECAP]` already asks for ("open on the setting") but had no data to satisfy:
    without it the director could describe *what* happened and never *where*. Advisory, like the
    Story Director's own `--- PHYSICAL SETTING ---` block, and for the same reason — it is prose
    generated from photograph counts, not a source of mediaIds, so it earns no special trust and
    changes no candidate's eligibility (`select.py` already settled that before this function runs).
    """
    profile = (event.get("eventTypeProfile") or {}) if isinstance(event, dict) else {}
    glossary = [str(g) for g in (profile.get("culturalGlossary") or [])]

    lines = [
        "--- COMMISSION ---",
        f"event={event.get('name')!r} type={profile.get('templateId') or 'generic'}",
        f"persona={persona.value}"
        + (f" stage={stage_id}" if stage_id else "")
        + (f" subject={names.get(person_id or '', person_id or '')}" if person_id else ""),
        f"styleSeed={seed} (use it to break ties differently than you otherwise would)",
        "",
        "--- YOUR MANDATE FOR THIS COMMISSION ---",
        PERSONA_LENS[persona],
    ]
    if venue:
        lines += ["", f"--- SETTING (advisory only) --- {venue}"]
    if diary:
        # The Event Diary (spec 13 §8): what each chapter felt like, written when it closed. The
        # recap film's brief is exactly what this exists for — same advisory posture as `venue`:
        # prose from an earlier distillation, never a source of mediaIds, changes no eligibility.
        lines += ["", "--- WHAT EACH CHAPTER FELT LIKE (advisory only — never a source of ids) ---"]
        lines += [f"- {sid}: {memo}" for sid, memo in sorted(diary.items())]
    if glossary:
        lines += [
            "",
            "--- CULTURAL GLOSSARY (the host wrote this; do not go beyond it) ---",
            ", ".join(glossary),
        ]
    lines += [
        "",
        f"--- EVIDENCE: {len(candidates)} eligible photographs, best first ---",
    ]
    lines += [_candidate_line(c, names) for c in candidates]
    if not candidates:
        lines.append("- (none: there is nothing to cut, return zero shots)")

    if previous_shot_ids:
        lines += [
            "",
            "--- YOUR PREVIOUS ATTEMPT ---",
            "shots were: " + ", ".join(previous_shot_ids),
        ]
    if critique:
        lines += [
            "",
            "--- THE CRITIC REJECTED THAT STORYBOARD. FIX EXACTLY THESE THINGS ---",
        ] + [f"- {issue}" for issue in critique] + [
            "Swap and re-order; do not rewrite the brief unless the critique says the story is wrong."
        ]
    return "\n".join(lines)


def prompt_parts(block: str) -> list[types.Part]:
    """One text part. The director never sees a photograph — it reads the Curator's description of
    one, which is the same discipline the Story Director's bounty validator follows (HANDOFF §4.24)
    and the reason `sa-render` is the only identity in the fleet that touches reel pixels."""
    return [as_text_part(block)]


#: The name `adk deploy agent_engine` looks for, kept as an alias for the same reason
#: `directors/story/agent.py` keeps one: the door to Agent Runtime stays open, not closed.
def root_agent() -> LlmAgent:
    return direct_agent()
