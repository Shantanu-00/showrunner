"use client";

// Why a wall needs an explicit audio-unlock module, and why the old three lines were not one.
//
// A kiosk is a television. Its only human gesture is the "Start show" tap, which happens minutes or
// hours before the first reel element mounts — so every reel premiere is an autoplay request with no
// gesture attached to it, and every browser gates audible autoplay behind exactly that.
//
// The previous approach failed twice over:
//
//  1. `KioskSetup` built an `AudioContext`, played a one-sample buffer, and dropped the reference on
//     the next line. It never called `resume()` (a context created inside a gesture can still start
//     `suspended`), and it was garbage-collectable immediately. Worse, unlocking a *WebAudio* context
//     does nothing for an `HTMLMediaElement` — they are separate permissions.
//  2. `ReelSlot` then started the video `muted` and set `.muted = false` in `onPlaying`. That is the
//     one pattern Chrome's autoplay policy specifically punishes: unmuting a media element that began
//     muted, without a transient activation, gets the element **paused** rather than unmuted. So the
//     wall reliably played either silence or nothing — which is what "no Lyria sound at all" was.
//
// What actually works is to spend the real gesture on the thing that needs it: call `play()` on an
// `HTMLAudioElement` *inside* the click handler. A media element that has successfully played once is
// permitted to play again, and in Chrome the click also grants the document sticky activation, which
// is what lets a later `<video autoplay>` start unmuted at all. Both effects are what we want, and
// both are only available during the gesture — which is why this cannot live in the reel component.
//
// Everything here is best-effort. A browser that refuses still leaves the wall working: `ReelSlot`
// falls back to muted playback and offers a one-tap "sound on" affordance, because a silent film on
// the wall is a degradation and a black rectangle is a bug.

/** One sample of silence, as a real WAV. Generated rather than pasted as a magic base64 string so the
 * header is readable: 44-byte canonical RIFF/WAVE, 8 kHz mono 8-bit, a handful of mid-scale (silent)
 * samples. Some browsers reject a zero-length media file, so it carries actual frames. */
function silentWavUrl(): string {
  const sampleRate = 8000;
  const frames = 800; // 0.1 s — long enough that `play()` is a real playback, short enough to ignore
  const dataSize = frames; // 8-bit mono: one byte per frame
  const buffer = new ArrayBuffer(44 + dataSize);
  const view = new DataView(buffer);
  const ascii = (offset: number, text: string) => {
    for (let i = 0; i < text.length; i += 1) view.setUint8(offset + i, text.charCodeAt(i));
  };
  ascii(0, "RIFF");
  view.setUint32(4, 36 + dataSize, true);
  ascii(8, "WAVE");
  ascii(12, "fmt ");
  view.setUint32(16, 16, true); // PCM header size
  view.setUint16(20, 1, true); // format = PCM
  view.setUint16(22, 1, true); // channels
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate, true); // byte rate (8-bit mono)
  view.setUint16(32, 1, true); // block align
  view.setUint16(34, 8, true); // bits per sample
  ascii(36, "data");
  view.setUint32(40, dataSize, true);
  // 8-bit PCM is unsigned: 128 is zero amplitude, so the file is genuinely silent.
  for (let i = 0; i < dataSize; i += 1) view.setUint8(44 + i, 128);
  return URL.createObjectURL(new Blob([buffer], { type: "audio/wav" }));
}

let unlocked = false;
let ctx: AudioContext | null = null;
let primer: HTMLAudioElement | null = null;
let primerUrl: string | null = null;
const listeners = new Set<(value: boolean) => void>();

function notify(): void {
  for (const listener of listeners) listener(unlocked);
}

/** Whether this page has audible-playback permission as far as we can tell. Read it when creating a
 * media element to decide its initial `muted`, never as a promise that sound will be heard —
 * `ReelSlot` still handles a rejected `play()`. */
export function isAudioUnlocked(): boolean {
  return unlocked;
}

/** Subscribe to the unlock flag. Returns the unsubscribe. */
export function onAudioUnlock(listener: (value: boolean) => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** Record that audible playback demonstrably works — called by `ReelSlot` when an unmuted element
 * actually reaches `playing`, so the flag reflects observed reality and not just a click. */
export function markAudioUnlocked(): void {
  if (unlocked) return;
  unlocked = true;
  notify();
}

/**
 * Spend a real user gesture on audio permission. **Must be called synchronously from inside a click
 * or touch handler** — awaiting something else first loses the activation that makes it work.
 *
 * Returns whether the priming `play()` was accepted. A `false` is not fatal: the flag is still set,
 * because the click itself may have granted the document what a later `<video>` needs, and the reel
 * has its own fallback either way.
 */
export async function unlockAudio(): Promise<boolean> {
  if (typeof window === "undefined") return false;

  // WebAudio: keep the context in module scope (the old code let it be collected) and resume it,
  // because a context can be created `suspended` even inside a gesture.
  try {
    const Ctor =
      window.AudioContext ??
      (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (Ctor) {
      ctx = ctx ?? new Ctor();
      if (ctx.state === "suspended") await ctx.resume();
      const source = ctx.createBufferSource();
      source.buffer = ctx.createBuffer(1, 1, 22050);
      source.connect(ctx.destination);
      source.start(0);
    }
  } catch {
    // A blocked or unavailable AudioContext changes nothing below.
  }

  // The half that actually matters for a `<video>`: play a real media element now, while the gesture
  // is live. The element is kept alive so the permission it just earned stays attached to something.
  let accepted = false;
  try {
    if (!primer) {
      primerUrl = silentWavUrl();
      primer = new Audio(primerUrl);
      primer.preload = "auto";
      primer.loop = false;
    }
    primer.muted = false;
    primer.volume = 0.001; // audible in principle, inaudible in the room
    await primer.play();
    primer.pause();
    primer.currentTime = 0;
    accepted = true;
  } catch {
    accepted = false;
  }

  unlocked = true;
  notify();
  return accepted;
}

/** Release the primer's blob URL. Only for a full teardown; the kiosk never unmounts in practice. */
export function releaseAudioUnlock(): void {
  if (primerUrl) URL.revokeObjectURL(primerUrl);
  primerUrl = null;
  primer = null;
}
