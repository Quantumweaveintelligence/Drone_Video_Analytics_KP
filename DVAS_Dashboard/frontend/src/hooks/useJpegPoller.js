/**
 * useJpegPoller.js
 * ───────────────────────────────────────────────────────────────
 * Unified JPEG polling hook for live drone feeds.
 * Replaces scattered, inconsistent implementations across the app.
 */

import { useState, useEffect, useRef, useCallback } from "react";
import { authFetch } from "@/lib/api";

const FEED_BACKOFF = [600, 1200, 2400, 5000, 10000, 20000, 30000];
const FEED_WATCHDOG_MS = 6000;

export function useJpegPoller(url, intervalMs = 600, enabled = true) {
  const [blobUrl, setBlobUrl] = useState(null);
  const [hasFrame, setHasFrame] = useState(false);
  const [feedDead, setFeedDead] = useState(false);
  const [fps, setFps] = useState(0);

  const timerRef = useRef(null);
  const watchRef = useRef(null);
  const errRef = useRef(0);
  const feedDeadRef = useRef(false);
  
  const mountedRef = useRef(true);
  const prevBlobRef = useRef(null);
  
  const fetchIdRef = useRef(0);
  const fpsCountRef = useRef(0);
  const fpsTimeRef = useRef(Date.now());
  
  // Extract meta (for CrowdAnalytics compatibility)
  const [meta, setMeta] = useState({});

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      clearTimeout(timerRef.current);
      clearTimeout(watchRef.current);
      if (prevBlobRef.current) URL.revokeObjectURL(prevBlobRef.current);
    };
  }, []);

  const doFetch = useCallback(async () => {
    if (!mountedRef.current) return;
    // Paused while the tab is hidden (saves bandwidth; lets the server-side viewer
    // heartbeat expire so unwatched analyzers can be throttled — RunPod plan 2.7/10.2).
    // The visibilitychange listener below resumes polling.
    if (typeof document !== "undefined" && document.hidden) return;
    
    fetchIdRef.current += 1;
    const currentFetchId = fetchIdRef.current;
    
    const sep = url.includes("?") ? "&" : "?";
    const fetchUrl = `${url}${sep}_t=${Date.now()}`;

    try {
      const res = await authFetch(fetchUrl);

      if (!mountedRef.current || fetchIdRef.current !== currentFetchId) return;

      if (res.ok) {
        const blob = await res.blob();
        if (blob.size === 0) {
          throw new Error("Empty blob");
        }
        
        const nextUrl = URL.createObjectURL(blob);
        
        // Deferred blob URL revocation to prevent flickering
        const oldUrl = prevBlobRef.current;
        prevBlobRef.current = nextUrl;
        
        setBlobUrl(nextUrl);
        setHasFrame(true);
        setFeedDead(false);
        errRef.current = 0;
        feedDeadRef.current = false;
        
        if (oldUrl) {
          requestAnimationFrame(() => {
            URL.revokeObjectURL(oldUrl);
          });
        }
        
        // Extract meta if any
        const crowdCount = res.headers.get('X-Crowd-Count');
        const vesselCount = res.headers.get('X-Vessel-Count');
        const densityLabel = res.headers.get('X-Density-Label');
        const droneId = res.headers.get('X-Drone-ID');
        const feedStatus = res.headers.get('X-Feed-Status') || res.headers.get('X-Stream-Status') || '';
        const frameAge = res.headers.get('X-Frame-Age') || '';

        setMeta({
          crowdCount: crowdCount != null ? parseFloat(crowdCount) : undefined,
          vesselCount: vesselCount != null ? parseInt(vesselCount, 10) : undefined,
          densityLabel: densityLabel || '',
          droneId: droneId || '',
          feedStatus,
          frameAge,
          _ts: Date.now(),
        });

        // FPS Calculation
        fpsCountRef.current += 1;
        const now = Date.now();
        if (now - fpsTimeRef.current >= 1000) {
          setFps(Math.round((fpsCountRef.current * 1000) / (now - fpsTimeRef.current)));
          fpsCountRef.current = 0;
          fpsTimeRef.current = now;
        }

        clearTimeout(watchRef.current);
        watchRef.current = setTimeout(() => {
          if (mountedRef.current) {
            setFeedDead(true);
            feedDeadRef.current = true;
          }
        }, FEED_WATCHDOG_MS);

        const isOffline = feedStatus === 'offline' || feedStatus === 'completed';
        const nextDelay = isOffline ? Math.max(intervalMs, 5000) : intervalMs;
        timerRef.current = setTimeout(doFetch, nextDelay);
      } else if (res.status === 404) {
        if (prevBlobRef.current) {
          URL.revokeObjectURL(prevBlobRef.current);
          prevBlobRef.current = null;
        }
        setBlobUrl(null);
        setHasFrame(false);
        setFeedDead(true);
        feedDeadRef.current = true;
        setMeta({});
        
        // Enter 3s slow-retry mode to avoid log spamming
        timerRef.current = setTimeout(doFetch, 3000);
      } else {
        throw new Error(`HTTP ${res.status}`);
      }
    } catch (e) {
      if (!mountedRef.current || fetchIdRef.current !== currentFetchId) return;
      
      errRef.current++;
      if (errRef.current > 3) {
        setFeedDead(true);
        feedDeadRef.current = true;
      }
      
      const delay = FEED_BACKOFF[Math.min(errRef.current - 1, FEED_BACKOFF.length - 1)];
      timerRef.current = setTimeout(doFetch, delay);
    }
  }, [url, intervalMs]);

  useEffect(() => {
    clearTimeout(timerRef.current);
    clearTimeout(watchRef.current);
    
    // Always revoke and clear previous blob when URL changes so old drone frame doesn't linger
    if (prevBlobRef.current) {
      URL.revokeObjectURL(prevBlobRef.current);
      prevBlobRef.current = null;
    }
    setBlobUrl(null);
    setHasFrame(false);

    if (!enabled || !url) {
      setFeedDead(!enabled);
      feedDeadRef.current = !enabled;
      errRef.current = 0;
      setFps(0);
      setMeta({});
      return;
    }

    errRef.current = 0;
    setFeedDead(false);
    feedDeadRef.current = false;
    doFetch();
    
  }, [enabled, url, doFetch]);

  // Pause/resume polling with page visibility
  useEffect(() => {
    if (typeof document === "undefined" || !enabled || !url) return undefined;
    const onVisibility = () => {
      clearTimeout(timerRef.current);
      clearTimeout(watchRef.current);   // don't flag the feed dead while hidden
      if (!document.hidden) doFetch();
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => document.removeEventListener("visibilitychange", onVisibility);
  }, [enabled, url, doFetch]);

  // For compatibility with components that expected `ok`
  const ok = hasFrame && !feedDead;

  return { blobUrl, hasFrame, feedDead, fps, ok, meta };
}
