import { useEffect, useRef, useState } from "react";
import { API, api } from "@/lib/api";

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
    let closed = false;
    let heartbeat;
    const connect = async () => {
      if (closed) return;
      
      const wsBase = API.replace(/^http/, "ws");
      const url = `${wsBase}/ws`;

      const ws = new WebSocket(url);
      ref.current = ws;
      ws.onopen = () => {
        setConnected(true);
        heartbeat = setInterval(() => { try { ws.send("ping"); } catch {} }, 20000);
      };
      ws.onmessage = (ev) => {
        try { cbRef.current?.(JSON.parse(ev.data)); } catch {}
      };
      ws.onclose = async (ev) => {
        setConnected(false);
        clearInterval(heartbeat);
        if (closed) return;

        // If auth expired (code 4401/4403/1008) or rejected connection (1006), attempt silent token refresh
        if (ev.code === 4401 || ev.code === 4403 || ev.code === 1008 || ev.code === 1006) {
          try {
            await api.post("/auth/refresh");
          } catch {}
        }
        setTimeout(connect, 3000);
      };
      ws.onerror = () => { try { ws.close(); } catch {} };
    };
    connect();
    return () => { closed = true; clearInterval(heartbeat); try { ref.current?.close(); } catch {} };
  }, []);

  return { connected };
}
