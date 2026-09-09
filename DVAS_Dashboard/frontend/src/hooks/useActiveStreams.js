/**
 * useActiveStreams.js
 * ───────────────────────────────────────────────────────────────
 * Polls /api/ingest-server/streams/active every 5 s to discover
 * which drones currently have frames arriving at the ingest server.
 *
 * Returns:
 *   streams      – raw array from ingest server (drone_id, mission_id, …)
 *   streamMap    – Map<drone_id, stream_obj> for O(1) look-up
 *   droneIsLive  – (id: string) => boolean
 *   mergeWithDrones – (dbDrones: Drone[]) => Drone[]  enriched + sorted
 *   loading      – bool
 *   error        – string | null
 */

import { useState, useEffect, useCallback, useRef } from "react";
import { api } from "@/lib/api";
import { useDvasSocket } from "@/lib/useDvasSocket";

const POLL_INTERVAL = 5000;   // 5 s — ingest status changes infrequently
const STALE_THRESHOLD = 20000; // 20 s — last_seen older than this → treat as stale

/** True if ISO timestamp is recent enough to be considered "live" */
function isRecentlySeen(lastSeen) {
  if (!lastSeen) return false;
  return Date.now() - new Date(lastSeen).getTime() < STALE_THRESHOLD;
}

/** Determine stream status: live vs draining vs offline */
function resolveDroneStatus(stream, fallbackStatus = "offline") {
  if (!stream) return fallbackStatus;
  
  if (stream.status === "draining") return "draining";  // draining takes priority
  const recent = isRecentlySeen(stream.last_seen);
  if (recent) return "live";
  if ((stream.messages_ready ?? 0) > 0) return "draining";
  return "offline";
}

export function useActiveStreams() {
  const [streams, setStreams]   = useState([]);
  const [loading, setLoading]   = useState(true);
  const [error, setError]       = useState(null);
  const mountedRef               = useRef(true);

  const fetchStreams = useCallback(async () => {
    try {
      const { data } = await api.get("/ingest-server/streams/active");
      if (!mountedRef.current) return;

      // Normalise — endpoint may return { drones: [...], count: N }
      const raw = Array.isArray(data) ? data : (data?.drones ?? []);
      setStreams(raw);
      setError(null);
    } catch (e) {
      if (!mountedRef.current) return;
      setError(e?.message || "Failed to fetch active streams");
      // Don't clear existing streams on transient error
    } finally {
      if (mountedRef.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    fetchStreams();
    const id = setInterval(fetchStreams, POLL_INTERVAL);
    return () => {
      mountedRef.current = false;
      clearInterval(id);
    };
  }, [fetchStreams]);

  // Subscribe to WebSocket for sub-second UI updates
  useDvasSocket((msg) => {
    if (msg.type === "drone_draining" || msg.event === "drone_draining") {
      setStreams(prev => prev.map(s => {
        if (s.drone_id === msg.drone_id) {
          return {
            ...s,
            status: "draining",
            messages_ready: msg.messages_ready ?? s.messages_ready,
            consumer_lag: msg.consumer_lag ?? s.consumer_lag
          };
        }
        return s;
      }));
    } else if (msg.type === "drone_offline" || msg.event === "drone_offline") {
      setStreams(prev => prev.map(s => {
        if (s.drone_id === msg.drone_id) {
          return { ...s, status: "offline" };
        }
        return s;
      }));
    }
  });

  /** O(1) lookup map: drone_id → stream object */
  const streamMap = new Map(streams.map((s) => [s.drone_id, s]));

  /** Is a given drone ID currently streaming live frames? */
  const droneIsLive = useCallback(
    (id) => {
      const s = streamMap.get(id);
      return !!s && isRecentlySeen(s.last_seen) && s.status !== "offline";
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [streams]
  );

  /**
   * Merge stream data into a DB drone array.
   *
   * Strategy:
   *  1. Start with the DB drones array (from /api/drones)
   *  2. For every stream drone NOT in DB, create a minimal record
   *  3. Enrich every record with live ingest metadata (mission_id, battery, GPS)
   *  4. Sort: live streams first, then draining, then degraded, then offline
   */
  const mergeWithDrones = useCallback(
    (dbDrones = []) => {
      const dbById = new Map(dbDrones.map((d) => [d.id, d]));

      // Build a combined set: DB drones + any stream-only drones
      const combined = [...dbDrones.map((d) => ({ ...d }))];
      for (const stream of streams) {
        if (!dbById.has(stream.drone_id)) {
          combined.push({
            id:            stream.drone_id,
            status:        resolveDroneStatus(stream, "offline"),
            battery_pct:   stream.battery_pct ?? null,
            lat:           stream.latitude  ?? null,
            lng:           stream.longitude ?? null,
            altitude_m:    stream.altitude_m ?? null,
            mission_id:    stream.mission_id ?? null,
            mission_name:  stream.mission_name ?? null,
            operator_name: stream.operator ?? null,
            jurisdiction:  stream.location ?? null,
            messages_ready: stream.messages_ready ?? 0,
            signal_strength: 85,
            uptime_min:    0,
            last_analytics: null,
          });
        }
      }

      // Enrich DB records with the freshest ingest data
      const enriched = combined.map((drone) => {
        const stream = streamMap.get(drone.id);
        if (!stream) {
          return {
            ...drone,
            messages_ready: drone.messages_ready ?? 0,
            status: drone.status === "live" && !drone.mission_id ? "offline" : (drone.status || "offline"),
          };
        }
        return {
          ...drone,
          battery_pct:   stream.battery_pct   ?? drone.battery_pct,
          lat:           stream.latitude       ?? drone.lat,
          lng:           stream.longitude      ?? drone.lng,
          altitude_m:    stream.altitude_m     ?? drone.altitude_m,
          abs_alt:       stream.abs_alt        ?? drone.abs_alt,
          gb_yaw:        stream.gb_yaw         ?? drone.gb_yaw,
          gb_pitch:      stream.gb_pitch       ?? drone.gb_pitch,
          gb_roll:       stream.gb_roll        ?? drone.gb_roll,
          focal_len:     stream.focal_len      ?? drone.focal_len,
          dzoom_ratio:   stream.dzoom_ratio    ?? drone.dzoom_ratio,
          gps_lock:      stream.gps_lock       ?? drone.gps_lock ?? true,
          mission_id:    stream.mission_id     ?? drone.mission_id,
          mission_name:  stream.mission_name   ?? drone.mission_name,
          operator_name: stream.operator       ?? drone.operator_name,
          jurisdiction:  stream.location       ?? drone.jurisdiction,
          last_seen:     stream.last_seen,
          messages_ready: stream.messages_ready ?? drone.messages_ready ?? 0,
          frame_count:   stream.frame_count    ?? stream.frames_received ?? 0,
          status:        resolveDroneStatus(stream, drone.status),
        };
      });

      // Sort: active live streams ALWAYS first → draining → degraded → offline; within tier: by id
      const statusOrder = { live: 0, draining: 1, degraded: 2, offline: 3 };
      enriched.sort((a, b) => {
        const aLiveStream = streamMap.has(a.id) && isRecentlySeen(streamMap.get(a.id)?.last_seen);
        const bLiveStream = streamMap.has(b.id) && isRecentlySeen(streamMap.get(b.id)?.last_seen);
        if (aLiveStream && !bLiveStream) return -1;
        if (!aLiveStream && bLiveStream) return 1;

        const ao = statusOrder[a.status] ?? 4;
        const bo = statusOrder[b.status] ?? 4;
        if (ao !== bo) return ao - bo;
        return (a.id || "").localeCompare(b.id || "");
      });

      return enriched;
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [streams]
  );

  return { streams, streamMap, droneIsLive, mergeWithDrones, loading, error };
}

