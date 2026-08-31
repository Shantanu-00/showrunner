"use client";

// The wall's background music: fetch the event's Lyria bed, loop it under the photographs, and get out
// of the way when a reel premieres.
//
// Three properties this has to have, in order of how badly getting them wrong would show in a room:
//
// 1. **It must never be the reason the wall has no sound *and* no picture.** Every failure — the event
//    has no track, Lyria is down, the browser refuses playback, the fetch 404s — resolves to silence and
//    nothing else. There is no error state, no retry storm and no UI.
// 2. **It must duck for a premiere.** A reel carries its own Lyria score, so two generated soundtracks
//    would otherwise play over each other. The caller passes `duck` and this fades out, pauses, and
//    fades back in afterwards — a hard cut on a music bed is more noticeable than the music itself.
// 3. **It must not fight the autoplay policy.** Audio needs the same gesture a reel's video does, so
//    this reuses `lib/audioUnlock.ts` rather than inventing a second unlock: nothing is even fetched
//    until the Start-show tap has happened, because a track fetched and then refused is bandwidth spent
//    on silence.
//
// The endpoint composes on first request for a given mood and answers `202` while it works, so a cold
// wall is quiet for one interval and then plays. That is deliberate (`backend/api/ambience.py`): the
// alternative is a request that blocks for tens of seconds while a television shows nothing.

import { useEffect, useRef, useState } from "react";
import { API_URL } from "./api";
import { isAudioUnlocked, onAudioUnlock } from "./audioUnlock";

/** Quiet enough to talk over — this is a bed, not a performance. */
const AMBIENCE_VOLUME = 0.32;
/** How long the fade in and out of a premiere takes, in ms. */
const FADE_MS = 900;
/** How long to wait before re-asking, when the server says it is still composing. */
const COMPOSING_RETRY_MS = 20_000;
/** How often to re-check for a *different* mood. The publisher changes the wall's stage on its own, and a
 * new stage can mean a new mood — but this is a background bed, so re-checking gently is correct. */
const MOOD_POLL_MS = 180_000;

interface AmbienceInfo {
  url: string;
  moodKey: string;
  tempoBpm?: number;
  caption?: string;
}

async function fetchAmbience(eventId: string): Promise<AmbienceInfo | "composing" | null> {
  try {
    const res = await fetch(`${API_URL}/v1/events/${eventId}/ambience?json=1`, {
      // No credentials: the open-event branch is deliberately unauthenticated, exactly like the reel
      // video a kiosk plays. An invite-only event answers 404 here and the wall stays quiet.
      cache: "no-store",
    });
    if (res.status === 202) return "composing";
    if (!res.ok) return null;
    const body = (await res.json()) as AmbienceInfo;
    return body?.url ? body : null;
  } catch {
    return null;
  }
}

/**
 * Play the event's ambient bed on a loop. Returns the current mood key, for anything that wants to
 * label what is playing.
 *
 * `duck` is for a reel premiere: pass `true` while one is on screen.
 */
export function useAmbience(eventId: string, { duck }: { duck: boolean }): string | null {
  const [unlocked, setUnlocked] = useState(() => isAudioUnlocked());
  const [info, setInfo] = useState<AmbienceInfo | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const fadeRef = useRef<number | null>(null);

  useEffect(() => onAudioUnlock(setUnlocked), []);

  // Resolve a track — only once audio can actually play, and re-checked on a slow interval so a stage
  // change eventually brings its own music.
  useEffect(() => {
    if (!unlocked || !eventId) return;
    let cancelled = false;
    let retry: ReturnType<typeof setTimeout> | null = null;

    const load = async () => {
      const result = await fetchAmbience(eventId);
      if (cancelled) return;
      if (result === "composing") {
        retry = setTimeout(load, COMPOSING_RETRY_MS);
        return;
      }
      // A null result leaves whatever is already playing alone: a transient 5xx should not silence a
      // wall that already has music.
      if (result) setInfo((prev) => (prev?.moodKey === result.moodKey ? prev : result));
    };

    void load();
    const poll = setInterval(() => void load(), MOOD_POLL_MS);
    return () => {
      cancelled = true;
      if (retry) clearTimeout(retry);
      clearInterval(poll);
    };
  }, [eventId, unlocked]);

  // One element for the life of the wall, its `src` swapped when the mood changes.
  useEffect(() => {
    if (!info?.url) return;
    let audio = audioRef.current;
    if (!audio) {
      audio = new Audio();
      audio.loop = true;
      audio.preload = "auto";
      audioRef.current = audio;
    }
    audio.src = info.url;
    audio.volume = duck ? 0 : AMBIENCE_VOLUME;
    if (!duck) void audio.play().catch(() => {});
    return () => {
      // Only on unmount of the whole wall — `info.url` changing swaps `src` above rather than tearing
      // the element down, so a mood change is a crossfade-free but gapless-enough swap.
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [info?.url]);

  // Fade for a premiere. Stepped rather than a CSS transition because `volume` is not animatable.
  useEffect(() => {
    const audio = audioRef.current;
    if (!audio || !info?.url) return;
    if (fadeRef.current) {
      clearInterval(fadeRef.current);
      fadeRef.current = null;
    }
    const target = duck ? 0 : AMBIENCE_VOLUME;
    const steps = 18;
    const stepMs = Math.max(16, Math.round(FADE_MS / steps));
    const start = audio.volume;
    let i = 0;
    if (!duck && audio.paused) void audio.play().catch(() => {});
    fadeRef.current = window.setInterval(() => {
      i += 1;
      const next = start + ((target - start) * i) / steps;
      audio.volume = Math.min(1, Math.max(0, next));
      if (i >= steps) {
        if (fadeRef.current) clearInterval(fadeRef.current);
        fadeRef.current = null;
        // Pause only *after* the fade, so ducking is never an audible cut.
        if (duck) audio.pause();
      }
    }, stepMs);
    return () => {
      if (fadeRef.current) clearInterval(fadeRef.current);
      fadeRef.current = null;
    };
  }, [duck, info?.url]);

  // Release the element when the wall itself goes away.
  useEffect(
    () => () => {
      const audio = audioRef.current;
      if (audio) {
        audio.pause();
        audio.src = "";
      }
      audioRef.current = null;
    },
    []
  );

  return info?.moodKey ?? null;
}
