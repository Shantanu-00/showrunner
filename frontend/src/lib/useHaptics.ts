"use client";

import { useCallback } from "react";

/**
 * Micro-Haptic hook (DESIGN_SYSTEM.md §5).
 * Safely invokes `navigator.vibrate` on supported devices (Android / PWA)
 * with graceful no-op on unsupported platforms (iOS Safari desktop, etc).
 */
export function useHaptics() {
  const triggerHaptic = useCallback((pattern: number | number[] = 15) => {
    if (typeof window !== "undefined" && "vibrate" in navigator) {
      try {
        navigator.vibrate?.(pattern);
      } catch {
        // Safe fallback if vibration permissions or hardware fail
      }
    }
  }, []);

  const tapHaptic = useCallback(() => triggerHaptic(15), [triggerHaptic]);
  const lightHaptic = useCallback(() => triggerHaptic(10), [triggerHaptic]);
  const successHaptic = useCallback(() => triggerHaptic([15, 30, 20]), [triggerHaptic]);
  const alertHaptic = useCallback(() => triggerHaptic([30, 40, 30]), [triggerHaptic]);

  return {
    triggerHaptic,
    tapHaptic,
    lightHaptic,
    successHaptic,
    alertHaptic,
  };
}
