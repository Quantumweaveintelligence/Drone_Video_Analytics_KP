/**
 * useAnalyticsStates.js
 * ───────────────────────────────────────────────────────────────
 * Per drone: is analytics keeping up with live video? (RunPod v2 plan R6.4)
 *   live      every enabled service is within 1.5 s of live
 *   degraded  a service is behind and skips its secondary stages (still every frame)
 *   shedding  a service drops the oldest frames or analyses every 2nd / 4th frame
 * GET /api/analytics/drone-states?ids=… every 3 s → {drone_id: {state, services: {svc: {state,
 * behind_s, frames_shed, cadence}}}}. Live video is never affected by these states.
 */

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

const POLL_MS = 3000;

export function useAnalyticsStates(droneIds) {
  const key = [...new Set(droneIds || [])].sort().join(",");
  const [states, setStates] = useState({});

  useEffect(() => {
    if (!key) {
      setStates({});
      return undefined;
    }
    let cancelled = false;
    const poll = async () => {
      try {
        const res = await api.get(`/analytics/drone-states?ids=${encodeURIComponent(key)}`);
        if (!cancelled) setStates(res.data || {});
      } catch {
        /* keep the last answer; the tiles just show no state */
      }
    };
    poll();
    const t = setInterval(poll, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [key]);

  return states;
}
