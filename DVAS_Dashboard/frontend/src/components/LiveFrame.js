/**
 * LiveFrame.js
 * ─────────────────────────────────────────────────────────────
 * Polls the ingest-server latest-JPEG endpoint for a given drone ID.
 *
 * Uses fetch() + explicit Authorization header (same pattern as
 * TrafficAnalytics.js useJpegPoller) so authentication works reliably
 * regardless of cookie state or cross-origin constraints.
 *
 * Props:
 *   droneId    - string  - drone ID to stream
 *   isOffline  - bool    - if true, show FEED OFFLINE immediately
 *   isDegraded - bool    - amber colour mode
 *   intervalMs - number  - poll interval (default 800 ms)
 */
import React, { useState, useEffect, useRef, useCallback } from "react";
import { API } from "@/lib/api";

// Backoff schedule (ms) for consecutive failures.
const BACKOFF_STEPS = [500, 1000, 2000, 4000, 8000, 16000, 30000];

// How long to wait without a new frame before marking feed dead (ms)
const WATCHDOG_MS = 6000;

export default function LiveFrame({
  droneId,
  isOffline,
  isDegraded,
  intervalMs = 800,
}) {
  const [blobUrl,  setBlobUrl]  = useState(null);
  const [hasFrame, setHasFrame] = useState(false);
  const [feedDead, setFeedDead] = useState(false);

  const timerRef     = useRef(null);
  const watchdogRef  = useRef(null);
  const errCountRef  = useRef(0);
  const mountedRef   = useRef(true);
  const prevBlobRef  = useRef(null);

  const clearTimers = useCallback(() => {
    clearTimeout(timerRef.current);
    clearTimeout(watchdogRef.current);
    timerRef.current    = null;
    watchdogRef.current = null;
  }, []);

  // Revoke the previous blob URL to avoid memory leaks
  const revokePrev = useCallback(() => {
    if (prevBlobRef.current) {
      URL.revokeObjectURL(prevBlobRef.current);
      prevBlobRef.current = null;
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      clearTimers();
      revokePrev();
    };
  }, [clearTimers, revokePrev]);

  useEffect(() => {
    if (isOffline) {
      clearTimers();
      revokePrev();
      setBlobUrl(null);
      setHasFrame(false);
      setFeedDead(true);
      return;
    }

    // Reset on drone change
    clearTimers();
    revokePrev();
    errCountRef.current = 0;
    setBlobUrl(null);
    setHasFrame(false);
    setFeedDead(false);

    const scheduleNext = (delay) => {
      if (!mountedRef.current) return;
      timerRef.current = setTimeout(doFetch, delay);
    };

    const doFetch = async () => {
      if (!mountedRef.current) return;

      const token  = localStorage.getItem("dvas_token") || "";
      const url    = `${API}/ingest-server/frame/${encodeURIComponent(droneId)}/latest?_t=${Date.now()}`;

      try {
        const res = await fetch(url, {
          headers: token ? { Authorization: `Bearer ${token}` } : {},
          credentials: "include",  // also send cookies (belt + suspenders)
        });

        if (!mountedRef.current) return;

        if (res.ok) {
          const blob    = await res.blob();
          const nextUrl = URL.createObjectURL(blob);

          revokePrev();
          prevBlobRef.current = nextUrl;

          setBlobUrl(nextUrl);
          setHasFrame(true);
          setFeedDead(false);
          errCountRef.current = 0;

          // Arm watchdog — if no new frame arrives within WATCHDOG_MS, mark dead
          clearTimeout(watchdogRef.current);
          watchdogRef.current = setTimeout(() => {
            if (mountedRef.current) setFeedDead(true);
          }, WATCHDOG_MS);

          scheduleNext(intervalMs);
        } else {
          throw new Error(`HTTP ${res.status}`);
        }
      } catch {
        if (!mountedRef.current) return;
        errCountRef.current += 1;
        if (errCountRef.current > 6) setFeedDead(true);
        const stepIdx = Math.min(errCountRef.current - 1, BACKOFF_STEPS.length - 1);
        scheduleNext(BACKOFF_STEPS[stepIdx]);
      }
    };

    // Kick off immediately
    doFetch();

    return () => {
      clearTimers();
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [droneId, isOffline, intervalMs]);

  /* ── Render ───────────────────────────────────────────────── */

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
            className="mono text-[8px] px-2 py-0.5 rounded flex items-center gap-1"
            style={{ background: "rgba(6,14,26,0.65)", color: "rgba(148,163,184,0.5)" }}
          >
            <span
              className="inline-block w-1 h-1 rounded-full animate-pulse"
              style={{ background: "rgba(148,163,184,0.4)" }}
            />
            AWAITING FEED…
          </div>
        </div>
      </>
    );
  }

  return (
    <>
      <img
        src={blobUrl}
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
