/**
 * Live edge data for every /edge page from ONE connection.
 *
 * Before: each page polled 2-3 edge endpoints every 2-3 s on its own (status, telemetry,
 * mission/active, analytics), each request crossing the launcher and the auth check.
 * Now: one Server-Sent Events stream (/api/edge/events) pushes a snapshot when it changes.
 * If the stream cannot be opened, the provider polls /api/edge/snapshot every 2 s and keeps
 * retrying the stream with back-off. Nothing runs while the browser tab is hidden.
 *
 * fetch() streaming is used instead of EventSource so the token travels in the
 * Authorization header, never in the URL (URLs end up in logs).
 */
import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { authFetch } from "@/lib/api";

const EVENTS_URL = "/edge-api/api/edge/events";
const SNAPSHOT_URL = "/edge-api/api/edge/snapshot";
const POLL_MS = 2000;
const RETRY_MIN_MS = 3000;
const RETRY_MAX_MS = 60000;

const EdgeLiveContext = createContext(null);

function parseSseBlock(block) {
  const data = block
    .split("\n")
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice(5).trimStart())
    .join("\n");
  return data ? JSON.parse(data) : null;
}

export function EdgeLiveProvider({ children }) {
  const [snapshot, setSnapshot] = useState(null);
  const [mode, setMode] = useState("connecting"); // connecting | stream | polling | paused
  const latest = useRef(null);

  useEffect(() => {
    let stopped = false;
    let controller = null;
    let pollTimer = null;
    let retryTimer = null;
    let retryMs = RETRY_MIN_MS;

    const apply = (snap) => {
      latest.current = snap;
      setSnapshot(snap);
    };

    const poll = async () => {
      try {
        const resp = await authFetch(SNAPSHOT_URL);
        if (resp.ok) apply(await resp.json());
      } catch (err) {
        console.warn("edge snapshot poll failed", err);
      }
    };
    const startPolling = () => {
      if (pollTimer) return;
      setMode("polling");
      poll();
      pollTimer = setInterval(poll, POLL_MS);
    };
    const stopPolling = () => {
      if (pollTimer) clearInterval(pollTimer);
      pollTimer = null;
    };

    const scheduleRetry = () => {
      if (stopped || document.hidden) return;
      startPolling();
      clearTimeout(retryTimer);
      retryTimer = setTimeout(stream, retryMs);
      retryMs = Math.min(RETRY_MAX_MS, retryMs * 2);
    };

    async function stream() {
      if (stopped || document.hidden) return;
      controller = new AbortController();
      try {
        const resp = await authFetch(EVENTS_URL, {
          signal: controller.signal,
          headers: { Accept: "text/event-stream" },
        });
        if (!resp.ok || !resp.body) throw new Error(`events HTTP ${resp.status}`);
        stopPolling();
        setMode("stream");
        retryMs = RETRY_MIN_MS;
        const reader = resp.body.pipeThrough(new TextDecoderStream()).getReader();
        let buf = "";
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          buf += value.replace(/\r\n/g, "\n");
          let cut;
          while ((cut = buf.indexOf("\n\n")) >= 0) {
            const block = buf.slice(0, cut);
            buf = buf.slice(cut + 2);
            try {
              const snap = parseSseBlock(block);
              if (snap) apply(snap);
            } catch (err) {
              console.warn("edge event parse failed", err);
            }
          }
        }
      } catch (err) {
        if (controller.signal.aborted) return;
        console.warn("edge event stream unavailable, polling instead", err);
      }
      scheduleRetry();
    }

    const onVisibility = () => {
      if (document.hidden) {
        controller?.abort();
        stopPolling();
        clearTimeout(retryTimer);
        setMode("paused");
      } else {
        retryMs = RETRY_MIN_MS;
        stream();
      }
    };

    document.addEventListener("visibilitychange", onVisibility);
    stream();
    return () => {
      stopped = true;
      controller?.abort();
      stopPolling();
      clearTimeout(retryTimer);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, []);

  const getSnapshot = useCallback(() => latest.current, []);

  return (
    <EdgeLiveContext.Provider value={{ snapshot, mode, getSnapshot }}>
      {children}
    </EdgeLiveContext.Provider>
  );
}

/** { snapshot, mode, getSnapshot } — snapshot = { status, telemetry, mission, analytics, uplink } or null. */
export function useEdgeLive() {
  const ctx = useContext(EdgeLiveContext);
  if (!ctx) throw new Error("useEdgeLive must be used inside <EdgeLiveProvider>");
  return ctx;
}
