"""Probe: will Nano Banana 2 edit a photograph that contains a person?

Spec 06 wants a stylized "claim your portrait" card in private albums — an edit of a real
guest photo. Person-editing is the policy question image models are touchiest about, so we
find out on B1 rather than on B4.

This probe does two calls:
  1. GENERATE a photoreal portrait of a synthetic person. Doubles as the shared test
     asset for the Veo and face probes — no real guest's face goes through a probe
     (HANDOFF §8 demo-dataset rule).
  2. EDIT that portrait (editorial B&W restyle, likeness preserved). Refusal here is the
     finding; the exact refusal text tells us whether it is a hard policy block or a
     promptable one.

Pass `--image <path>` to run step 2 against your own photo instead — a truer test of the
policy, since a synthetic face may be treated differently from a real one.

Portrait styling is already P2 (the lost-day pre-cut, EXECUTION-PLAN §0), so NO-GO here
costs nothing. This probe confirms the cut is safe; it does not unblock a P0.

Discovered call shape (2026-08-27):
  * Nano Banana is NOT on the Interactions API — `interactions.create` rejects it with
    "Unsupported model interaction". Use the classic `models.generate_content`.
  * `location` must be `global`; us-central1 404s ("Publisher model ... not found").
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _harness as H

# Image models are not served from the regional Vertex endpoints.
IMAGE_LOCATION = "global"

GENERATE_PROMPT = (
    "Photorealistic candid wedding-guest portrait of a fictional woman in her late "
    "twenties, South Asian, warm brown eyes, wearing a gold-embroidered maroon saree, "
    "soft evening string lights behind her, shallow depth of field, 50mm lens, natural "
    "skin texture, looking slightly off-camera, half-smiling. Photograph, not "
    "illustration. Vertical 3:4 framing, head and shoulders."
)

EDIT_PROMPT = (
    "Restyle this photograph as a high-contrast editorial black-and-white film portrait: "
    "deep blacks, soft highlight roll-off, subtle 35mm grain. Keep the person's face, "
    "identity, pose and expression exactly as they are — change only the grade and "
    "lighting mood. Do not alter facial features."
)


def _split(response) -> tuple[bytes, str, str]:
    """Pull (image bytes, mime, any text) out of a generate_content response."""
    image, mime, text = b"", "", ""
    for candidate in response.candidates or []:
        for part in (candidate.content.parts if candidate.content else []) or []:
            inline = getattr(part, "inline_data", None)
            if inline and inline.data and not image:
                image, mime = inline.data, inline.mime_type or ""
            if getattr(part, "text", None):
                text += part.text
    return image, mime, text


def _call(client, prompt: str, image_bytes: bytes | None = None, mime: str = "image/png"):
    from google.genai import types

    contents: list = [prompt]
    if image_bytes is not None:
        contents.append(types.Part.from_bytes(data=image_bytes, mime_type=mime))
    return client.models.generate_content(
        model=H.env("MODEL_IMAGE_EDIT"),
        contents=contents,
        config=types.GenerateContentConfig(response_modalities=["IMAGE", "TEXT"]),
    )


def _finish_reason(response) -> str:
    for candidate in response.candidates or []:
        if candidate.finish_reason:
            return str(candidate.finish_reason)
    return "unset"


def body(v: H.Verdict) -> None:
    client = H.enterprise_client(IMAGE_LOCATION)
    v.note(f"endpoint: enterprise/Vertex generate_content, location={IMAGE_LOCATION}")

    # --- step 1: generate the synthetic cast portrait -------------------------------
    source = H.image_arg()
    if source:
        v.note(f"using supplied photo instead of generating: {source.name}")
        original, original_mime = source.read_bytes(), "image/jpeg" if source.suffix.lower() in {".jpg", ".jpeg"} else "image/png"
    else:
        gen = _call(client, GENERATE_PROMPT)
        v.cost_usd += 0.045
        original, original_mime, gen_text = _split(gen)
        v.note(f"generate: finish_reason={_finish_reason(gen)} mime={original_mime or 'none'}")
        if not original:
            v.verdict = H.NO_GO
            v.headline = (
                "refused / returned no image even for a *synthetic* person generation: "
                f"{gen_text[:200]!r}"
            )
            return
        v.evidence.append(H.save_bytes(H.PORTRAIT, original))
        v.note("generating a photoreal synthetic person: ALLOWED")

    # --- step 2: the actual policy question -----------------------------------------
    edit = _call(client, EDIT_PROMPT, original, original_mime or "image/png")
    v.cost_usd += 0.045
    edited, edited_mime, edit_text = _split(edit)
    v.note(f"edit: finish_reason={_finish_reason(edit)} mime={edited_mime or 'none'}")
    if edit_text.strip():
        v.note(f"model text alongside the edit: {edit_text.strip()[:200]!r}")

    if edited:
        v.evidence.append(H.save_bytes("cast_portrait_styled.png", edited))
        v.verdict = H.GO
        v.headline = (
            f"person-edit ALLOWED — {len(edited) / 1024:.0f} KB restyled portrait returned "
            f"(${v.cost_usd:.3f} for both calls). The P2 'claim your portrait' card is "
            "technically available if a day finishes early."
        )
    else:
        v.verdict = H.NO_GO
        v.headline = f"person-edit refused / no image returned. Model said: {edit_text[:250]!r}"
        v.note(
            "No schedule impact: Nano Banana portraits were already pre-cut to P2 and carry no "
            "bonus points (the +0.6 model cap is already met by Lyria/Veo/Gemma)."
        )


if __name__ == "__main__":
    H.run(
        "banana",
        "Will Nano Banana 2 (gemini-3.1-flash-image) restyle a photo containing a person?",
        body,
        gate="NO-GO = confirms the existing P2 cut; no replan needed. Also produces the "
        "synthetic cast portrait the Veo and face probes reuse.",
    )
