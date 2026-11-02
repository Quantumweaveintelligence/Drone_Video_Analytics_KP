import { useEffect, useRef, useState } from "react";
import { backoffMs, refreshSession, wsUrl } from "@/lib/api";

/**
 * Live WebSocket connection to /api/ws (cookie auth; the server filters messages per user).
 *
 * One connection is shared by every component that uses the hook (AppShell bell, command
 * context, pages); it opens with the first subscriber, closes with the last, and
 * reconnects with exponential backoff. onMessage(msg) is called for every server frame.
 */
const subscribers = new Set();          // { onMessage(msg), onStatus(bool) }
let socket = null;
let heartbeat = null;
let retryTimer = null;
let attempt = 0;
let isOpen = false;

const setStatus = (open) => {
  isOpen = open;
  subscribers.forEach((s) => s.onStatus(open));
};

function connect() {
  if (socket || subscribers.size === 0) return;
  let ws;
  try {
    ws = new WebSocket(wsUrl("/ws"));
  } catch (err) {
    console.warn("WebSocket initialization failed on this client:", err);
    return;
  }
  socket = ws;
  ws.onopen = () => {
    attempt = 0;
    setStatus(true);
    heartbeat = setInterval(() => { try { ws.send("ping"); } catch {} }, 20000);
  };
  ws.onmessage = (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch { return; }
    subscribers.forEach((s) => { try { s.onMessage(msg); } catch (e) { console.error(e); } });
  };
  ws.onclose = async (ev) => {
    clearInterval(heartbeat);
    socket = null;
    setStatus(false);
    if (subscribers.size === 0) return;
    // 4401 = missing/expired session: refresh the cookie once before reconnecting
    if (ev.code === 4401) {
      try { await refreshSession(); } catch { /* the API interceptor sends the user to login */ }
    }
    clearTimeout(retryTimer);
    retryTimer = setTimeout(connect, backoffMs(attempt++));
  };
  ws.onerror = () => { try { ws.close(); } catch {} };
}

function disconnect() {
  clearTimeout(retryTimer);
  clearInterval(heartbeat);
  const ws = socket;
  socket = null;
  attempt = 0;
  try { ws?.close(); } catch {}
}

export function useDvasSocket(onMessage) {
  const [connected, setConnected] = useState(isOpen);
  const cbRef = useRef(onMessage);
  cbRef.current = onMessage;

  useEffect(() => {
    const sub = { onMessage: (msg) => cbRef.current?.(msg), onStatus: setConnected };
    subscribers.add(sub);
    setConnected(isOpen);
    connect();
    return () => {
      subscribers.delete(sub);
      if (subscribers.size === 0) disconnect();
    };
  }, []);

  return { connected };
}
