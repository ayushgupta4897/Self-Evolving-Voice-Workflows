"use client";

import { useEffect, useRef, useState } from "react";
import type { StatePayload } from "./types";

/**
 * Polls /api/state every 2s.
 *
 * Two stage-safety rules:
 *  - A failed fetch never blanks the screen. The last good payload stays up and
 *    only `stale` flips.
 *  - Generations merge by number rather than replacing the array wholesale, so a
 *    checkpoint file caught mid-write (and therefore skipped by the route) does
 *    not make a generation vanish and reappear in front of an audience.
 */
export function useSwarmState(intervalMs = 2000) {
  const [data, setData] = useState<StatePayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [stale, setStale] = useState(false);
  const [loading, setLoading] = useState(true);
  const last = useRef<StatePayload | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function tick() {
      try {
        const res = await fetch("/api/state", { cache: "no-store" });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const next: StatePayload = await res.json();
        if (cancelled) return;

        const prev = last.current;
        if (prev && prev.source === next.source) {
          const merged = new Map(prev.generations.map((g) => [g.number, g]));
          for (const g of next.generations) merged.set(g.number, g);
          next.generations = [...merged.values()].sort((a, b) => a.number - b.number);
        }

        last.current = next;
        setData(next);
        setError(null);
        setStale(false);
      } catch (err) {
        if (cancelled) return;
        setError((err as Error).message);
        if (last.current) setStale(true);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    tick();
    const id = setInterval(tick, intervalMs);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [intervalMs]);

  return { data, error, stale, loading };
}
