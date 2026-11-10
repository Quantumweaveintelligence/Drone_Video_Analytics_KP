/**
 * useLiveVideo.js
 * ───────────────────────────────────────────────────────────────
 * Continuous live video of a drone (RunPod v2 plan R2).
 *
 * When the edge streams H.264 (v2 uplink), ingest relays it to go2rtc and the browser
 * plays it at the drone's own frame rate: fMP4 over one WebSocket into Media Source
 * Extensions (/live/api/ws?src=<slot>, go2rtc's MSE protocol). nginx lets the socket open
 * only for a logged-in user who may see the drone (session cookie; ?token= on the edge app).
 *
 * state: "off" (not enabled) | "connecting" | "playing" | "unavailable" (the drone sends
 * no video — older edge on JPEG uploads — or MSE is missing: callers keep their JPEG view).
 * Lookups are retried every 15 s while unavailable; a stalled or closed socket reconnects.
 */

import { useEffect, useRef, useState } from "react";
import { API, authFetch, getAuthToken } from "@/lib/api";

const CODECS = ["avc1.640029", "avc1.64002A", "avc1.640033", "avc1.42E01E", "avc1.4D401F",
                "hvc1.1.6.L153.B0", "mp4a.40.2", "mp4a.40.5", "flac", "opus"];
const LOOKUP_RETRY_MS = 15000;
const RECONNECT_MS = [1000, 2000, 4000, 8000];
const STALL_MS = 8000;           // no video data for this long → reconnect
const MAX_LAG_S = 1.5;           // behind the newest buffered frame by more → jump to it
const KEEP_S = 10;               // buffered seconds kept behind the playhead

function MediaSourceClass() {
  return window.ManagedMediaSource || window.MediaSource || null;
}

export function liveWsUrl(path) {
  const origin = API.startsWith("http")
    ? API.replace(/\/api\/?$/, "").replace(/^http/, "ws")
    : `${window.location.protocol === "https:" ? "wss:" : "ws:"}//${window.location.host}`;
  const token = getAuthToken();                       // edge build only (cookie otherwise)
  const url = `${origin}${path}`;
  return token ? `${url}${url.includes("?") ? "&" : "?"}token=${encodeURIComponent(token)}` : url;
}

function supportedCodecs(MS) {
  return CODECS.filter((c) => {
    try {
      return MS.isTypeSupported(`video/mp4; codecs="${c}"`);
    } catch {
      return false;
    }
  }).join(",");
}

export function useLiveVideo(droneId, enabled = true) {
  const videoRef = useRef(null);
  const [state, setState] = useState("off");
  const [info, setInfo] = useState(null);

  useEffect(() => {
    if (!enabled || !droneId) {
      setState("off");
      setInfo(null);
      return undefined;
    }
    const MS = MediaSourceClass();
    if (!MS) {
      setState("unavailable");
      return undefined;
    }
    let cancelled = false;
    let ws = null;
    let ms = null;
    let sb = null;
    let objectUrl = null;
    let queue = [];
    let timer = null;
    let watchdog = null;
    let lastData = 0;
    let failures = 0;
    let everPlayed = false;

    const later = (fn, ms_) => {
      clearTimeout(timer);
      timer = setTimeout(() => { if (!cancelled) fn(); }, ms_);
    };

    const teardown = () => {
      clearInterval(watchdog);
      watchdog = null;
      if (ws) {
        ws.onopen = ws.onmessage = ws.onclose = ws.onerror = null;
        try { ws.close(); } catch { /* already closed */ }
      }
      ws = null;
      sb = null;
      queue = [];
      if (ms && ms.readyState === "open") {
        try { ms.endOfStream(); } catch { /* ignore */ }
      }
      ms = null;
      const v = videoRef.current;
      if (v) {
        v.removeAttribute("src");
        try { v.load(); } catch { /* ignore */ }
      }
      if (objectUrl) URL.revokeObjectURL(objectUrl);
      objectUrl = null;
    };

    const pump = () => {
      if (!sb || sb.updating) return;
      const v = videoRef.current;
      try {
        if (v && sb.buffered.length && v.currentTime - sb.buffered.start(0) > KEEP_S + 5) {
          sb.remove(sb.buffered.start(0), v.currentTime - KEEP_S);
          return;                                    // onupdateend → pump again
        }
        if (queue.length) {
          const total = queue.reduce((n, b) => n + b.byteLength, 0);
          const data = new Uint8Array(total);
          let off = 0;
          for (const b of queue) { data.set(new Uint8Array(b), off); off += b.byteLength; }
          queue = [];
          sb.appendBuffer(data);
        }
      } catch (e) {
        if (e && e.name === "QuotaExceededError" && v && sb.buffered.length) {
          try { sb.remove(sb.buffered.start(0), Math.max(sb.buffered.start(0) + 0.1, v.currentTime - 2)); } catch { /* ignore */ }
        } else {
          reconnect();
        }
      }
    };

    const keepLive = () => {
      const v = videoRef.current;
      if (!v || !sb) return;
      if (Date.now() - lastData > STALL_MS) {
        reconnect();
        return;
      }
      try {
        if (sb.buffered.length) {
          const end = sb.buffered.end(sb.buffered.length - 1);
          if (end - v.currentTime > MAX_LAG_S) v.currentTime = Math.max(0, end - 0.3);
        }
      } catch { /* buffered can throw while the source closes */ }
      if (v.paused) v.play().catch(() => {});
    };

    function reconnect() {
      teardown();
      if (cancelled) return;
      failures += 1;
      if (!everPlayed && failures >= 3) {
        setState("unavailable");
        failures = 0;
        later(lookup, LOOKUP_RETRY_MS * 2);
        return;
      }
      setState("connecting");
      later(lookup, RECONNECT_MS[Math.min(failures - 1, RECONNECT_MS.length - 1)]);
    }

    const connect = (wsPath) => {
      teardown();
      ws = new WebSocket(liveWsUrl(wsPath));
      ws.binaryType = "arraybuffer";
      lastData = Date.now();
      ws.onopen = () => ws.send(JSON.stringify({ type: "mse", value: supportedCodecs(MS) }));
      ws.onmessage = (ev) => {
        if (typeof ev.data === "string") {
          let msg;
          try { msg = JSON.parse(ev.data); } catch { return; }
          if (msg.type === "error") { reconnect(); return; }
          if (msg.type !== "mse" || ms) return;
          ms = new MS();
          ms.addEventListener("sourceopen", () => {
            try {
              sb = ms.addSourceBuffer(msg.value);
              sb.mode = "segments";
              sb.addEventListener("updateend", pump);
              pump();
            } catch {
              reconnect();
            }
          }, { once: true });
          const v = videoRef.current;
          if (!v) return;
          v.disableRemotePlayback = true;            // ManagedMediaSource (Safari) needs this
          objectUrl = URL.createObjectURL(ms);
          v.src = objectUrl;
          v.play().catch(() => {});
          return;
        }
        lastData = Date.now();
        queue.push(ev.data);
        pump();
      };
      ws.onclose = () => reconnect();
      ws.onerror = () => {};
      watchdog = setInterval(keepLive, 1000);
    };

    async function lookup() {
      if (cancelled) return;
      try {
        const res = await authFetch(`${API}/live/drone/${encodeURIComponent(droneId)}`);
        if (cancelled) return;
        if (!res.ok) {
          setState("unavailable");
          later(lookup, LOOKUP_RETRY_MS);
          return;
        }
        const data = await res.json();
        if (cancelled) return;
        setInfo(data);
        setState((s) => (s === "playing" ? s : "connecting"));
        connect(data.ws_path);
      } catch {
        if (!cancelled) {
          setState("unavailable");
          later(lookup, LOOKUP_RETRY_MS);
        }
      }
    }

    const onPlaying = () => {
      everPlayed = true;
      failures = 0;
      setState("playing");
    };
    const v = videoRef.current;
    v?.addEventListener("playing", onPlaying);
    setState("connecting");
    lookup();

    return () => {
      cancelled = true;
      clearTimeout(timer);
      v?.removeEventListener("playing", onPlaying);
      teardown();
    };
  }, [droneId, enabled]);

  return { videoRef, state, info, playing: state === "playing" };
}
