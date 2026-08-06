"use client";

/**
 * Motion helpers for the polish layer (spec 7).
 *
 * Two rules from the spec shape everything here. Entrance animation runs
 * **once**, over about half a second - it is roughly 80% of the "cool" and it
 * is cheap. And there is **one hero moment per story**: the biggest insight
 * earns real flair and everything else stays calm, because if everything
 * moves, nothing lands.
 *
 * All of it respects `prefers-reduced-motion`. Worth being explicit about why
 * that needs code: the global stylesheet zeroes CSS animation and transition
 * durations, but Framer Motion and the count-up below animate in JavaScript by
 * writing inline styles frame by frame, so a CSS rule cannot touch them. The
 * media query has to be read directly.
 */

import { useEffect, useRef, useState } from "react";
import { useReducedMotion } from "framer-motion";

/** Entrance duration. Spec 7: once, about half a second. */
export const ENTRANCE_MS = 480;
/** Chart draw-in, matched to the entrance so a card settles as one thing. */
export const CHART_MS = 520;

/**
 * Count a number up on first appearance.
 *
 * Eased out rather than linear: a linear counter reads like a loading spinner,
 * while decelerating reads like a value arriving and settling. Returns the
 * target immediately when reduced motion is requested, so the number is simply
 * correct rather than briefly wrong.
 */
export function useCountUp(
  target: number,
  { durationMs = ENTRANCE_MS, enabled = true }: { durationMs?: number; enabled?: boolean } = {}
): number {
  const reduced = useReducedMotion();
  const [value, setValue] = useState(() => (reduced || !enabled ? target : 0));
  const frame = useRef<number | null>(null);

  useEffect(() => {
    if (reduced || !enabled || !Number.isFinite(target)) {
      setValue(target);
      return;
    }

    const start = performance.now();
    const from = 0;

    const tick = (now: number) => {
      const progress = Math.min(1, (now - start) / durationMs);
      // Cubic ease-out.
      const eased = 1 - Math.pow(1 - progress, 3);
      setValue(from + (target - from) * eased);
      if (progress < 1) {
        frame.current = requestAnimationFrame(tick);
      }
    };

    frame.current = requestAnimationFrame(tick);
    return () => {
      if (frame.current !== null) cancelAnimationFrame(frame.current);
    };
  }, [target, durationMs, enabled, reduced]);

  return value;
}

/**
 * Standard card entrance: a short rise and fade, once, when scrolled into view.
 *
 * `stagger` is capped deliberately. Uncapped, the tenth card waits most of a
 * second before it moves, which reads as jank rather than choreography.
 */
export function entrance(index = 0, reduced = false) {
  if (reduced) {
    return { initial: { opacity: 1 }, whileInView: { opacity: 1 }, transition: { duration: 0 } };
  }
  return {
    initial: { opacity: 0, y: 14 },
    whileInView: { opacity: 1, y: 0 },
    viewport: { once: true, margin: "-60px" } as const,
    transition: {
      duration: ENTRANCE_MS / 1000,
      delay: Math.min(index * 0.04, 0.2),
      ease: [0.16, 1, 0.3, 1] as [number, number, number, number],
    },
  };
}

/** True when the visitor asked for less movement. */
export function usePrefersReducedMotion(): boolean {
  return useReducedMotion() ?? false;
}
