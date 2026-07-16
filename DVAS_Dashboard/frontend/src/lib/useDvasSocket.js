import { useEffect, useRef, useState } from "react";
import { API } from "@/lib/api";

/**
 * Live WebSocket connection to /api/ws.
 * onMessage(msg) called for every server frame.
 */
export function useDvasSocket(onMessage) {
  const [connected, setConnected] = useState(false);
  const ref = useRef(null);
  const cbRef = useRef(onMessage);
  cbRef.current = onMessage;

  useEffect(() => {
    const token = localStorage.getItem("dvas_token");
    if (!token) return;
    const wsBase = API.replace(/^http/, "ws");
    const url = `${wsBase}/ws?token=${encodeURIComponent(token)}`;

    let closed = false;
    let heartbeat;
    const connect = () => {
      const ws = new WebSocket(url);
      ref.current = ws;
      ws.onopen = () => {
        setConnected(true);
        heartbeat = setInterval(() => { try { ws.send("ping"); } catch {} }, 20000);
      };
      ws.onmessage = (ev) => {
        try { cbRef.current?.(JSON.parse(ev.data)); } catch {}
      };
      ws.onclose = () => {
        setConnected(false);
        clearInterval(heartbeat);
        if (!closed) setTimeout(connect, 3000);
      };
      ws.onerror = () => { try { ws.close(); } catch {} };
    };
    connect();
    return () => { closed = true; clearInterval(heartbeat); try { ref.current?.close(); } catch {} };
  }, []);

  return { connected };
}
