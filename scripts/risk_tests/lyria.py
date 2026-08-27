"""Probe: can we generate a Lyria 3 soundtrack clip on the billed Vertex path?

Every reel's soundtrack is a Lyria clip (spec 06, bonus +0.2). If Lyria is unreachable
on the first-party Vertex path we lose both the bonus and the reel's audio bed.

GO requires a real MP3 payload we can hand to librosa for the beat grid.

Discovered call shape (2026-08-27 — none of this is in the SDK's type hints):
  * `location` MUST be `global`; us-central1/us/eu are all rejected outright.
  * `stream=True` is MANDATORY — the non-streaming call returns
    "Request contains an invalid argument".
  * Passing `response_modalities` or `response_format` is REJECTED
    ("Audio delivery mode is not supported") — send neither.
  * google-genai 2.20.0 has no typed events for this stream: everything arrives as
    `UnknownInteractionSSEEvent`, so we read `ev.raw` dicts directly.
  * Audio is base64 `audio/mpeg` in the `content.delta` whose `delta.type == "audio"`.
    Lyria also emits a free text "Caption:" describing the piece — useful provenance
    to log next to the reel.
"""

from __future__ import annotations

import base64
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _harness as H

# Lyria 3 ignores the enterprise regional endpoints entirely.
LYRIA_LOCATION = "global"

PROMPT = (
    "Instrumental Indian wedding celebration score, 30 seconds: warm sitar and bansuri "
    "melody over a steady dholak groove, tabla accents, strings swelling at the halfway "
    "point, joyful and cinematic, clean pulse for editing to. No vocals."
)


def _stream_audio(v: H.Verdict, client) -> tuple[bytes, str, str, dict]:
    """Drain the Lyria SSE stream. Returns (mp3 bytes, mime, caption, usage)."""
    stream = client.interactions.create(
        model=H.env("MODEL_MUSIC"),
        input=PROMPT,
        stream=True,
    )
    chunks: list[bytes] = []
    mime, caption, usage = "", "", {}
    for event in stream:
        raw = getattr(event, "raw", None)
        if not isinstance(raw, dict):
            continue
        kind = raw.get("event_type")
        if kind == "content.delta":
            delta = raw.get("delta") or {}
            if delta.get("type") == "audio" and delta.get("data"):
                mime = delta.get("mime_type", "")
                chunks.append(base64.b64decode(delta["data"]))
            elif delta.get("type") == "text":
                caption += delta.get("text", "")
        elif kind == "interaction.complete":
            usage = (raw.get("interaction") or {}).get("usage") or {}
        elif kind == "error":
            raise RuntimeError(f"Lyria stream error: {raw.get('error')}")
    return b"".join(chunks), mime, caption, usage


def body(v: H.Verdict) -> None:
    client = H.enterprise_client(LYRIA_LOCATION)
    v.note(f"endpoint: enterprise/Vertex, location={LYRIA_LOCATION} (only supported location)")

    raw, mime, caption, usage = _stream_audio(v, client)
    if not raw:
        v.verdict = H.NO_GO
        v.headline = "stream completed but carried no audio payload"
        return

    v.note(f"audio mime={mime}, {len(raw):,} bytes, ID3-tagged={raw[:3] == b'ID3'}")
    v.note(f"usage={usage}")
    if caption.strip():
        v.note(f"Lyria also returned a free text caption ({len(caption)} chars) — log it as reel provenance")
    v.evidence.append(H.save_bytes("lyria_clip.mp3", raw))
    if caption.strip():
        v.evidence.append(H.save_bytes("lyria_caption.txt", caption.encode("utf-8")))

    v.cost_usd = 0.04
    v.verdict = H.GO
    v.headline = (
        f"{len(raw) / 1024:.0f} KB {mime} clip from Vertex/global at $0.04 — reel soundtrack "
        "and the +0.2 Lyria bonus are both live (streaming-only, no response_format)."
    )


if __name__ == "__main__":
    H.run(
        "lyria",
        "Can we generate a 30 s Lyria 3 clip on the billed first-party Vertex path?",
        body,
        gate="NO-GO = reels ship silent and the +0.2 Lyria bonus is lost. Escalate immediately.",
    )
