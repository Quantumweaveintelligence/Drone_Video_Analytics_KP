import { useEffect, useRef, useState, useCallback } from "react";
import { api, backoffMs, refreshSession, wsUrl } from "@/lib/api";

/**
 * useIngestServer — custom hook for real-time ingest server monitoring.
 *
 * Provides:
 *  - health:        { reachable, status, uptime_s, version } polled every 10s
 *  - status:        Full system status from /api/ingest-server/status (polled every 5s)
 *  - drones:        Parsed drone array from status
 *  - wsEvents:      Array of recent WebSocket events (max 50)
 *  - wsConnected:   Whether the WebSocket relay is connected
 *  - testConnection: Async function to manually test readiness
 *  - readiness:     Last readiness check result
 *  - loading:       True while initial health fetch is in progress
 *  - fps:           Calculated frames/second from WebSocket event rate
 */
export function useIngestServer() {
  const [health, setHealth] = useState(null);
  const [status, setStatus] = useState(null);
  const [readiness, setReadiness] = useState(null);
  const [wsEvents, setWsEvents] = useState([]);
  const [wsConnected, setWsConnected] = useState(false);
  const [loading, setLoading] = useState(true);
  const [fps, setFps] = useState(0);

  const wsRef = useRef(null);
  const fpsCountRef = useRef(0);
  const fpsTimerRef = useRef(null);

  // ── Poll health (every 10s) ───────────────────────────────────────────────
  useEffect(() => {
    let mounted = true;
    const fetchHealth = async () => {
      try {
        // admin-only detail (version, uptime, URL); the public /health is status-only
        const { data } = await api.get("/ingest-server/health/detail");
        if (mounted) {
          setHealth(data);
          setLoading(false);
        }
      } catch {
        if (mounted) {
          setHealth({ reachable: false, status: "unreachable" });
          setLoading(false);
        }
      }
    };
    fetchHealth();
    const interval = setInterval(() => { if (!document.hidden) fetchHealth(); }, 10000);
    return () => { mounted = false; clearInterval(interval); };
  }, []);

  // ── Poll status (every 5s) ────────────────────────────────────────────────
  useEffect(() => {
    let mounted = true;
    const fetchStatus = async () => {
      try {
        const { data } = await api.get("/ingest-server/status");
        if (mounted) setStatus(data);
      } catch {
        if (mounted) setStatus(null);
      }
    };
    fetchStatus();
    const interval = setInterval(() => { if (!document.hidden) fetchStatus(); }, 5000);
    return () => { mounted = false; clearInterval(interval); };
  }, []);

  // ── FPS calculation (sliding 1-second window) ────────────────────────────
  useEffect(() => {
    fpsTimerRef.current = setInterval(() => {
      setFps(fpsCountRef.current);
      fpsCountRef.current = 0;
    }, 1000);
    return () => clearInterval(fpsTimerRef.current);
  }, []);

  // ── WebSocket connection to ingest server relay ───────────────────────────
  useEffect(() => {
    let closed = false;
    let heartbeat;

    let attempt = 0;
    let retryTimer;

    const connect = () => {
      if (closed) return;
      const ws = new WebSocket(wsUrl("/ingest-server/ws"));   // cookie auth, no token in the URL
      wsRef.current = ws;

      ws.onopen = () => {
        attempt = 0;
        setWsConnected(true);
        heartbeat = setInterval(() => {
          try { ws.send("ping"); } catch {}
        }, 20000);
      };

      ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data);
          // Track frame events for FPS calculation
          if (msg.event === "frame_queued") {
            fpsCountRef.current += 1;
          }
          // Don't store keepalive/pong events
          if (msg.event === "keepalive" || msg.event === "pong") return;

          setWsEvents((prev) => [
            { ...msg, _received: Date.now() },
            ...prev,
          ].slice(0, 50));
        } catch {}
      };

      ws.onclose = async (ev) => {
        setWsConnected(false);
        clearInterval(heartbeat);
        if (closed) return;

        if (ev.code === 4401) {
          try { await refreshSession(); } catch {}
        }
        retryTimer = setTimeout(connect, backoffMs(attempt++));
      };

      ws.onerror = () => {
        try { ws.close(); } catch {}
      };
    };

    connect();
    return () => {
      closed = true;
      clearInterval(heartbeat);
      clearTimeout(retryTimer);
      try { wsRef.current?.close(); } catch {}
    };
  }, []);

  // ── Test connection (manual readiness check) ──────────────────────────────
  const testConnection = useCallback(async () => {
    try {
      const { data } = await api.get("/ingest-server/ready");
      setReadiness(data);
      return data;
    } catch (err) {
      // MON-30: readiness details are admin-only; a 403 is not a connection failure
      const status = err?.response?.status;
      const result = status === 403
        ? { reachable: null, ready: null, detail: "Readiness details are visible to administrators only" }
        : { reachable: false, ready: false, detail: status ? `Ingest server error (${status})` : "Connection failed" };
      setReadiness(result);
      return result;
    }
  }, []);

  // ── Derived: drone list from status ───────────────────────────────────────
  const drones = status?.drones || [];

  return {
    health,
    status,
    drones,
    readiness,
    wsEvents,
    wsConnected,
    loading,
    fps,
    testConnection,
  };
}
