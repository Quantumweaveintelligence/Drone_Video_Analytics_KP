import React, { useState, useEffect, useRef, useCallback } from "react";
import { API } from "@/lib/api";

// Backoff schedule (ms) for consecutive 404/error responses.
// After BACKOFF_STEPS.length failures we stay at the last (max) interval.
const BACKOFF_STEPS = [500, 1000, 2000, 4000, 8000, 16000, 30000];

export default function LiveFrame({ droneId, isOffline, isDegraded }) {
  const [imgSrc,   setImgSrc]   = useState(null);
  const [hasFrame, setHasFrame] = useState(false);
  const [feedDead, setFeedDead] = useState(false);

  // Refs so callbacks always see current values without re-registering effects
  const timerRef     = useRef(null);   // setTimeout handle (replaces setInterval)
  const failCountRef = useRef(0);
  const deadTimerRef = useRef(null);
  const mountedRef   = useRef(true);

  // Clear all pending timers
  const clearTimers = useCallback(() => {
    if (timerRef.current)     clearTimeout(timerRef.current);
    if (deadTimerRef.current) clearTimeout(deadTimerRef.current);
    timerRef.current     = null;
    deadTimerRef.current = null;
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      clearTimers();
    };
  }, [clearTimers]);

  useEffect(() => {
    if (isOffline) {
      clearTimers();
      return;
    }

    // Reset state when droneId changes
    failCountRef.current = 0;
    setFeedDead(false);
    setHasFrame(false);
    setImgSrc(null);
    clearTimers();

    const scheduleNext = (delayMs) => {
      if (!mountedRef.current) return;
      timerRef.current = setTimeout(load, delayMs);
    };

    const load = () => {
      if (!mountedRef.current) return;

      const ts  = Date.now();
      const url = `${API}/ingest-server/frame/${droneId}/latest?t=${ts}`;
      const img = new Image();

      img.onload = () => {
        if (!mountedRef.current) return;
        failCountRef.current = 0;
        setImgSrc(url);
        setHasFrame(true);
        setFeedDead(false);

        // Arm a "feed died" watchdog — if no new frame arrives in 3 s, mark dead
        if (deadTimerRef.current) clearTimeout(deadTimerRef.current);
        deadTimerRef.current = setTimeout(() => {
          if (mountedRef.current) setFeedDead(true);
        }, 3000);

        // Poll again at normal speed
        scheduleNext(500);
      };

      img.onerror = () => {
        if (!mountedRef.current) return;
        failCountRef.current += 1;

        // After 6 failures show FEED OFFLINE and slow right down
        if (failCountRef.current > 6) {
          setFeedDead(true);
        }

        // Exponential backoff — cap at last step
        const stepIdx = Math.min(failCountRef.current - 1, BACKOFF_STEPS.length - 1);
        const delay   = BACKOFF_STEPS[stepIdx];
        scheduleNext(delay);
      };

      img.src = url;
    };

    // Kick off immediately
    load();

    return () => {
      clearTimers();
    };
  }, [droneId, isOffline, clearTimers]); // eslint-disable-line react-hooks/exhaustive-deps

  /* ── Render ────────────────────────────────────────────── */

  if (isOffline || feedDead) {
    return (
      <>
        <div className="absolute inset-0 dvas-grid-bg opacity-40" />
        <div className="absolute inset-0 flex items-center justify-center">
          <div className="mono text-[10px] text-red-500 bg-black/60 px-2 py-1 rounded border border-red-500/30">
            FEED OFFLINE
          </div>
        </div>
      </>
    );
  }

  if (!hasFrame) {
    return (
      <>
        <div
          className="absolute inset-0 opacity-20"
          style={{
            background: isDegraded
              ? "radial-gradient(ellipse at 50% 50%, rgba(245,158,11,0.3) 0%, transparent 70%)"
              : "radial-gradient(ellipse at 50% 50%, rgba(20,184,166,0.15) 0%, transparent 70%)",
          }}
        />
        <div className="absolute bottom-6 left-0 right-0 flex justify-center">
          <div
            className="mono text-[8px] px-2 py-0.5 rounded"
            style={{ background: "rgba(6,14,26,0.65)", color: "rgba(148,163,184,0.5)" }}
          >
            AWAITING FEED…
          </div>
        </div>
      </>
    );
  }

  return (
    <>
      <img
        src={imgSrc}
        alt={`Live feed ${droneId}`}
        className="absolute inset-0 w-full h-full object-cover"
        style={{ opacity: 0.95 }}
      />
      <div className="absolute bottom-4 left-0 right-0 flex justify-center pointer-events-none">
        <div
          className="mono text-[8px] px-2 py-0.5 rounded flex items-center gap-1"
          style={{ background: "rgba(6,14,26,0.75)", color: isDegraded ? "#fbbf24" : "#2dd4bf" }}
        >
          <span
            className="inline-block w-1.5 h-1.5 rounded-full"
            style={{ background: isDegraded ? "#fbbf24" : "#2dd4bf", animation: "pulse 1s infinite" }}
          />
          LIVE FEED · {droneId}
        </div>
      </div>
    </>
  );
}
