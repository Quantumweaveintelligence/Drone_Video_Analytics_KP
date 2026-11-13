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
 * options.sub: play the 720p grid sub-stream (<slot>_sub) instead of the source — what grid
 * tiles use, so several tiles fit a control-room link and a laptop's decoder. The lookup is
 * repeated every 20 s while it plays: that keeps the server's transcode running.
 *
 * Staying live without visible skips (R2 hardening): a little behind the live edge the player
 * speeds up slightly (up to 1.25×) instead of seeking; it seeks only when far behind. A short
 * reconnect keeps the last frame on screen ("reconnecting" counts as playing) instead of
 * falling back to JPEG.
 *
 * state: "off" (not enabled) | "connecting" | "playing" | "reconnecting" | "unavailable" (the
 * drone sends no video — older edge on JPEG uploads — or MSE is missing: callers keep their
 * JPEG view). Lookups are retried every 15 s while unavailable.
 *
 * stats (only with the debug switch: localStorage dvas.liveDebug = "1", or ?livedebug=1):
 * rendered fps, dropped frames, seconds behind the live edge, received kbps, reconnects,
 * seeks, playback rate — the numbers that tell network, decoder and player problems apart.
 */

import { useEffect, useRef, useState } from "react";
import { API, authFetch, getAuthToken } from "@/lib/api";

const CODECS = ["avc1.640029", "avc1.64002A", "avc1.640033", "avc1.42E01E", "avc1.4D401F",
                "hvc1.1.6.L153.B0", "mp4a.40.2", "mp4a.40.5", "flac", "opus"];
const LOOKUP_RETRY_MS = 15000;
const SUB_REFRESH_MS = 20000;    // repeat the ?sub=1 lookup while playing (server keeps 45 s)
const SUB_PENDING_MS = 3000;     // R9: the sub-stream is starting — play the main stream, ask again soon
const STARTING_MS = 1000;        // R9: the stream is not published yet — ask again, nothing failed
const MAX_STARTING = 30;         // … for at most this many tries, then count as a failure
const RECONNECT_MS = [1000, 2000, 4000, 8000];
const STALL_MS = 8000;           // no video data for this long → reconnect
const RECONNECT_GRACE_MS = 10000; // keep the last frame this long before showing "connecting"
const TARGET_LAG_S = 0.5;        // where the playhead sits behind the newest buffered frame
const SEEK_LAG_S = 5;            // further behind than this → seek (a visible skip) instead of speeding up
const MAX_RATE = 1.25;
const KEEP_S = 10;               // buffered seconds kept behind the playhead

function MediaSourceClass() {
  return window.ManagedMediaSource || window.MediaSource || null;
}

export function liveDebugEnabled() {
  try {
    return new URLSearchParams(window.location.search).get("livedebug") === "1"
      || window.localStorage.getItem("dvas.liveDebug") === "1";
  } catch {
    return false;
  }
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

export function useLiveVideo(droneId, enabled = true, options = {}) {
  const sub = !!options.sub;
  const videoRef = useRef(null);
  const [state, setState] = useState("off");
  const [info, setInfo] = useState(null);
  const [stats, setStats] = useState(null);

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
    const debug = liveDebugEnabled();
    let cancelled = false;
    let ws = null;
    let ms = null;
    let sb = null;
    let objectUrl = null;
    let queue = [];
    let timer = null;
    let watchdog = null;
    let refresher = null;
    let pendingSub = false;          // asked for the sub-stream, the server has not started it yet
    let graceTimer = null;
    let lastData = 0;
    let failures = 0;
    let startingTries = 0;          // consecutive "not published yet" answers (R9)
    let everPlayed = false;
    let currentPath = null;
    const counters = { bytes: 0, reconnects: 0, seeks: 0, reason: "" };
    let sample = null;                                // previous stats sample

    const later = (fn, ms_) => {
      clearTimeout(timer);
      timer = setTimeout(() => { if (!cancelled) fn(); }, ms_);
    };

    // keepFrame: leave the last decoded frame on screen (a reconnect, not a stop)
    const teardown = (keepFrame = false) => {
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
      if (v && !keepFrame) {
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
          reconnect("append failed");
        }
      }
    };

    const behindLive = () => {
      const v = videoRef.current;
      try {
        return v && sb && sb.buffered.length ? sb.buffered.end(sb.buffered.length - 1) - v.currentTime : 0;
      } catch {
        return 0;                                    // buffered can throw while the source closes
      }
    };

    const keepLive = () => {
      const v = videoRef.current;
      if (!v || !sb) return;
      if (Date.now() - lastData > STALL_MS) {
        reconnect(`no video for ${STALL_MS / 1000} s`);
        return;
      }
      const lag = behindLive();
      if (lag > SEEK_LAG_S) {
        try { v.currentTime = Math.max(0, v.currentTime + lag - TARGET_LAG_S); counters.seeks += 1; } catch { /* ignore */ }
        v.playbackRate = 1;
      } else if (lag > TARGET_LAG_S + 0.3) {
        // drift back to the live edge: faster the further behind, never a jump
        v.playbackRate = Math.min(MAX_RATE, 1 + (lag - TARGET_LAG_S) * 0.15);
      } else if (v.playbackRate !== 1 && lag <= TARGET_LAG_S) {
        v.playbackRate = 1;
      }
      if (v.paused) v.play().catch(() => {});
      if (debug) {
        const q = v.getVideoPlaybackQuality ? v.getVideoPlaybackQuality() : null;
        const now = performance.now();
        if (q && sample) {
          const dt = (now - sample.t) / 1000;
          setStats({
            fps: Math.round(((q.totalVideoFrames - sample.frames) / dt) * 10) / 10,
            dropped: q.droppedVideoFrames,
            behind: Math.round(lag * 100) / 100,
            kbps: Math.round(((counters.bytes - sample.bytes) * 8) / dt / 1000),
            reconnects: counters.reconnects,
            reason: counters.reason,
            seeks: counters.seeks,
            rate: Math.round(v.playbackRate * 100) / 100,
            res: `${v.videoWidth}x${v.videoHeight}`,
          });
        }
        if (q) sample = { t: now, frames: q.totalVideoFrames, bytes: counters.bytes };
      }
    };

    // R9: go2rtc has no publisher for the stream yet (mission just started, sub-stream starting).
    // Not a failure: keep the last frame, ask again in a second.
    function notYet() {
      startingTries += 1;
      if (startingTries > MAX_STARTING) {
        startingTries = 0;
        reconnect("stream never started");
        return;
      }
      teardown(everPlayed);
      if (cancelled) return;
      setState((s) => (s === "playing" ? "reconnecting" : s === "reconnecting" ? s : "connecting"));
      later(lookup, STARTING_MS);
    }

    // reason (R9): why the tile reconnected — shown in the debug overlay and the console
    function reconnect(reason = "") {
      const keep = everPlayed;
      teardown(keep);
      if (cancelled) return;
      failures += 1;
      counters.reconnects += 1;
      counters.reason = reason;
      console.info(`[live] ${droneId}${sub ? " sub" : ""}: reconnect #${counters.reconnects} — ${reason || "unknown"}`);
      if (!everPlayed && failures >= 3) {
        setState("unavailable");
        failures = 0;
        later(lookup, LOOKUP_RETRY_MS * 2);
        return;
      }
      if (keep) {
        // keep showing the last frame for a while; only then let the caller fall back to JPEG
        setState((s) => (s === "playing" ? "reconnecting" : s));
        clearTimeout(graceTimer);
        graceTimer = setTimeout(() => {
          if (!cancelled) setState((s) => (s === "reconnecting" ? "connecting" : s));
        }, RECONNECT_GRACE_MS);
      } else {
        setState("connecting");
      }
      later(lookup, RECONNECT_MS[Math.min(failures - 1, RECONNECT_MS.length - 1)]);
    }

    const connect = (wsPath) => {
      teardown(everPlayed);
      currentPath = wsPath;
      ws = new WebSocket(liveWsUrl(wsPath));
      ws.binaryType = "arraybuffer";
      lastData = Date.now();
      ws.onopen = () => ws.send(JSON.stringify({ type: "mse", value: supportedCodecs(MS) }));
      ws.onmessage = (ev) => {
        if (typeof ev.data === "string") {
          let msg;
          try { msg = JSON.parse(ev.data); } catch { return; }
          if (msg.type === "error") {
            // "mse: streams: …": nobody publishes this stream yet — wait, it is not a failure
            if (/streams/.test(msg.value || "")) { notYet(); return; }
            reconnect(`server: ${msg.value || "error"}`);
            return;
          }
          if (msg.type !== "mse" || ms) return;
          ms = new MS();
          ms.addEventListener("sourceopen", () => {
            try {
              sb = ms.addSourceBuffer(msg.value);
              sb.mode = "segments";
              sb.addEventListener("updateend", pump);
              pump();
            } catch {
              reconnect("codec not supported");
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
        counters.bytes += ev.data.byteLength;
        queue.push(ev.data);
        pump();
      };
      ws.onclose = (ev) => reconnect(`socket closed (${ev.code})`);
      ws.onerror = () => {};
      watchdog = setInterval(keepLive, 1000);
    };

    const lookupUrl = () => `${API}/live/drone/${encodeURIComponent(droneId)}${sub ? "?sub=1" : ""}`;

    async function lookup() {
      if (cancelled) return;
      try {
        const res = await authFetch(lookupUrl());
        if (cancelled) return;
        if (!res.ok) {
          setState((s) => (s === "reconnecting" ? s : "unavailable"));
          later(lookup, LOOKUP_RETRY_MS);
          return;
        }
        const data = await res.json();
        if (cancelled) return;
        pendingSub = !!data.sub_pending;
        if (data.starting) { notYet(); return; }      // published in a moment: do not connect yet
        setInfo(data);
        setState((s) => (s === "playing" || s === "reconnecting" ? s : "connecting"));
        connect(data.ws_path);
      } catch {
        if (!cancelled) {
          setState((s) => (s === "reconnecting" ? s : "unavailable"));
          later(lookup, LOOKUP_RETRY_MS);
        }
      }
    }

    // ?sub=1 keeps the server's transcode alive; a changed slot (new mission) reconnects.
    // R9: the server hands out the sub-stream only once it publishes (sub_pending until then),
    // so the tile plays the main stream first and switches over a few seconds later.
    if (sub) {
      const refresh = async () => {
        if (cancelled) return;
        if (ws) {
          try {
            const res = await authFetch(lookupUrl());
            if (res.ok && !cancelled) {
              const data = await res.json();
              pendingSub = !!data.sub_pending;
              if (!cancelled && !data.starting && data.ws_path && data.ws_path !== currentPath) connect(data.ws_path);
            }
          } catch { /* the watchdog handles a dead stream */ }
        }
        if (!cancelled) refresher = setTimeout(refresh, pendingSub ? SUB_PENDING_MS : SUB_REFRESH_MS);
      };
      refresher = setTimeout(refresh, SUB_PENDING_MS);
    }

    const onPlaying = () => {
      everPlayed = true;
      failures = 0;
      startingTries = 0;
      clearTimeout(graceTimer);
      setState("playing");
    };
    const v = videoRef.current;
    v?.addEventListener("playing", onPlaying);
    setState("connecting");
    lookup();

    return () => {
      cancelled = true;
      clearTimeout(timer);
      clearTimeout(graceTimer);
      clearTimeout(refresher);
      v?.removeEventListener("playing", onPlaying);
      teardown();
    };
  }, [droneId, enabled, sub]);

  const playing = state === "playing" || state === "reconnecting";
  return { videoRef, state, info, playing, stats };
}
