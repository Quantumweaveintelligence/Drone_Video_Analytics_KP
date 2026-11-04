/**
 * useLiveFeed.js
 * ───────────────────────────────────────────────────────────────
 * Live annotated video for the analytics pages.
 *
 * Polling one JPEG at a time (useJpegPoller) capped the live view at ~1 fps: every
 * frame cost a full round trip through the RunPod proxy plus the poll interval. This
 * hook opens ONE continuous MJPEG stream (/api/analytics/<service>/annotated/stream),
 * shows each new frame as it arrives (up to the analyzer's rate, 20–30 fps), and reads
 * the analyzer's real processing rate from the part headers (X-Proc-Fps).
 *
 * Same return shape as useJpegPoller (+ procFps, mode). Falls back to polling the
 * given URL when the stream endpoint is unavailable (older backend).
 */

import { useEffect, useRef, useState } from "react";
import { authFetch } from "@/lib/api";
import { useJpegPoller } from "@/hooks/useJpegPoller";

const RETRY_MS = [500, 1000, 2000, 4000, 8000];
const FALLBACK_AFTER = 3;          // consecutive failed connects → polling
const STALL_MS = 6000;             // no frame for this long → feed marked dead
const MAX_BUFFER = 8 * 1024 * 1024;
const enc = new TextEncoder();
const dec = new TextDecoder();

/** "/api/analytics/<svc>/annotated[/latest]?drone_id=X" → "/api/analytics/<svc>/annotated/stream?drone_id=X" */
export function streamUrlFor(pollUrl) {
  if (!pollUrl) return null;
  const m = pollUrl.match(/^(.*\/analytics\/(person|vehicle|anpr|intrusion|vessel|anomaly)\/annotated)(?:\/latest)?(\?.*)?$/);
  return m ? `${m[1]}/stream${m[3] || ""}` : null;
}

function indexOf(buf, pat, from = 0) {
  outer: for (let i = from; i <= buf.length - pat.length; i++) {
    for (let j = 0; j < pat.length; j++) if (buf[i + j] !== pat[j]) continue outer;
    return i;
  }
  return -1;
}

/** Pull complete parts out of the buffer: [{ headers, body }], rest */
export function parseParts(buf) {
  const parts = [];
  const CRLF2 = enc.encode("\r\n\r\n");
  let pos = 0;
  for (;;) {
    const start = indexOf(buf, enc.encode("--"), pos);
    if (start < 0) break;
    const headEnd = indexOf(buf, CRLF2, start);
    if (headEnd < 0) break;
    const headers = {};
    dec.decode(buf.subarray(start, headEnd)).split("\r\n").slice(1).forEach((line) => {
      const i = line.indexOf(":");
      if (i > 0) headers[line.slice(0, i).trim().toLowerCase()] = line.slice(i + 1).trim();
    });
    const len = parseInt(headers["content-length"], 10);
    if (!Number.isFinite(len) || len < 0) { pos = headEnd + 4; continue; }
    const bodyStart = headEnd + 4;
    if (buf.length < bodyStart + len) break;          // wait for the rest of this frame
    parts.push({ headers, body: buf.slice(bodyStart, bodyStart + len) });
    pos = bodyStart + len;
  }
  return { parts, rest: buf.slice(pos) };
}

export function useLiveFeed(pollUrl, intervalMs = 800, enabled = true) {
  const streamUrl = streamUrlFor(pollUrl);
  const [useFallback, setUseFallback] = useState(!streamUrl);
  const [blobUrl, setBlobUrl] = useState(null);
  const [hasFrame, setHasFrame] = useState(false);
  const [feedDead, setFeedDead] = useState(false);
  const [fps, setFps] = useState(0);
  const [procFps, setProcFps] = useState(0);
  const [meta, setMeta] = useState({});
  const prevBlob = useRef(null);

  // Polling fallback (only active when the stream is unavailable)
  const poll = useJpegPoller(pollUrl, intervalMs, enabled && useFallback);

  useEffect(() => { setUseFallback(!streamUrl); }, [streamUrl]);

  useEffect(() => {
    if (!enabled || !streamUrl || useFallback) return undefined;
    let cancelled = false;
    let ctrl = null;
    let retryTimer = null;
    let stallTimer = null;
    let failures = 0;
    let frameTimes = [];

    const clearFrame = () => {
      if (prevBlob.current) URL.revokeObjectURL(prevBlob.current);
      prevBlob.current = null;
      setBlobUrl(null);
      setHasFrame(false);
    };

    const armStall = () => {
      clearTimeout(stallTimer);
      stallTimer = setTimeout(() => { if (!cancelled) setFeedDead(true); }, STALL_MS);
    };

    const show = ({ headers, body }) => {
      const url = URL.createObjectURL(new Blob([body], { type: "image/jpeg" }));
      const old = prevBlob.current;
      prevBlob.current = url;
      setBlobUrl(url);
      setHasFrame(true);
      setFeedDead(false);
      if (old) requestAnimationFrame(() => URL.revokeObjectURL(old));
      const now = performance.now();
      frameTimes = frameTimes.filter((t) => now - t < 2000).concat(now);
      setFps(frameTimes.length > 1 ? Math.round(((frameTimes.length - 1) * 1000) / (now - frameTimes[0])) : 0);
      const pf = parseFloat(headers["x-proc-fps"]);
      if (Number.isFinite(pf)) setProcFps(pf);
      setMeta({ frameAge: headers["x-frame-age"] || "", seq: headers["x-frame-seq"] || "", _ts: Date.now() });
      armStall();
    };

    const connect = async () => {
      if (cancelled) return;
      if (typeof document !== "undefined" && document.hidden) return;   // resumed on visibility
      ctrl = new AbortController();
      try {
        const res = await authFetch(streamUrl, { signal: ctrl.signal });
        if (!res.ok || !res.body) throw Object.assign(new Error(`HTTP ${res.status}`), { status: res.status });
        failures = 0;
        armStall();
        const reader = res.body.getReader();
        let buf = new Uint8Array(0);
        for (;;) {
          const { value, done } = await reader.read();
          if (done || cancelled) break;
          const merged = new Uint8Array(buf.length + value.length);
          merged.set(buf);
          merged.set(value, buf.length);
          const { parts, rest } = parseParts(merged);
          buf = rest.length > MAX_BUFFER ? new Uint8Array(0) : rest;
          if (parts.length) show(parts[parts.length - 1]);   // only the newest frame is drawn
        }
        // Stream ended (analyzer idle / max duration): reconnect promptly
        if (!cancelled) retryTimer = setTimeout(connect, RETRY_MS[0]);
      } catch (e) {
        if (cancelled || e.name === "AbortError") return;
        failures += 1;
        // 404/405: no stream route (older backend); repeated failures: a proxy that
        // breaks streaming → poll single frames instead
        if (e.status === 404 || e.status === 405 || failures >= FALLBACK_AFTER) {
          setUseFallback(true);
          return;
        }
        if (failures > 1) setFeedDead(true);
        retryTimer = setTimeout(connect, RETRY_MS[Math.min(failures - 1, RETRY_MS.length - 1)]);
      }
    };

    const onVisibility = () => {
      clearTimeout(retryTimer);
      clearTimeout(stallTimer);
      if (document.hidden) { if (ctrl) ctrl.abort(); }
      else connect();
    };

    clearFrame();
    setFeedDead(false);
    setFps(0);
    connect();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      cancelled = true;
      document.removeEventListener("visibilitychange", onVisibility);
      clearTimeout(retryTimer);
      clearTimeout(stallTimer);
      if (ctrl) ctrl.abort();
      clearFrame();
    };
  }, [streamUrl, enabled, useFallback]);

  if (useFallback) return { ...poll, procFps: 0, mode: "poll" };
  return { blobUrl, hasFrame, feedDead, fps, procFps, ok: hasFrame && !feedDead, meta, mode: "stream" };
}
