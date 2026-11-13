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
 *   isGridDrone  – (drone) => boolean  the grid/live-count rule (see below)
 *   loading      – bool
 *   error        – string | null
 */

import { useState, useEffect, useCallback, useRef } from "react";
import { api } from "@/lib/api";
import { useDvasSocket } from "@/lib/useDvasSocket";

const POLL_INTERVAL = 5000;   // 5 s — ingest status changes infrequently
const STALE_THRESHOLD = 20000; // 20 s — last_seen older than this → treat as stale

/**
 * True if ingest saw the drone recently enough to be "live".
 * v2 R9: measured on ONE clock. Ingest reports seen_age_s on its own clock; the poll stamps
 * _rx_at on the browser's; the age now is seen_age_s + the time since that poll. Comparing
 * last_seen (server clock) with Date.now() (viewer clock) made every tile flip live / offline
 * on each poll when the two clocks were ≈ 15 s apart. last_seen is only the fallback (older
 * ingest, the backend's /ingest/status fallback).
 */
export function isStreamRecent(stream, now = Date.now()) {
  if (!stream) return false;
  if (typeof stream.seen_age_s === "number" && typeof stream._rx_at === "number") {
    const age = stream.seen_age_s * 1000 + Math.max(0, now - stream._rx_at);
    return age >= 0 && age < STALE_THRESHOLD;
  }
  if (!stream.last_seen) return false;
  const diff = now - new Date(stream.last_seen).getTime();
  return diff >= 0 && diff < STALE_THRESHOLD;
}

// Statuses that get a tile in the Multi-Drone Grid / Multi-Drone View
export const GRID_STATUSES = ["live", "degraded", "draining"];

/**
 * The one rule for grid tiles AND the live-stream counter (analytics plan A5): the drone
 * still has an ingest stream and is live, degraded or draining. A drone whose stream was
 * drained or purged has no tile, so nothing polls a frame that no longer exists.
 */
export function isGridDrone(drone, streamMap) {
  return !!drone && !!streamMap?.has(drone.id) && GRID_STATUSES.includes(drone.status);
}

/** Frames still queued for a draining drone (consumer lag when known). */
export function framesLeft(drone) {
  if (drone?.consumer_lag && typeof drone.consumer_lag === "object") {
    return Object.values(drone.consumer_lag).reduce((sum, v) => sum + (Number(v) || 0), 0);
  }
  return drone?.messages_ready ?? 0;
}

/** Determine stream status: live vs draining vs offline */
export function resolveDroneStatus(stream, fallbackStatus = "offline") {
  if (!stream) return fallbackStatus;
  if (stream._offline) return "offline";                // drone_offline pushed over the WebSocket

  if (stream.status === "draining") return "draining";  // draining takes priority
  const recent = isStreamRecent(stream);
  const frames = stream.frame_count ?? stream.frames_received ?? 0;
  // v2 R9: a drone streaming video with no analytics service selected has no analytics
  // frames (R6.2) but is live — its video uplink is open
  if (recent && (frames > 0 || stream.video_live)) return "live";
  if ((stream.messages_ready ?? 0) > 0 || frames > 0) return "draining";
  return "offline";
}

export function useActiveStreams() {
  const [streams, setStreams]   = useState([]);
  const [loading, setLoading]   = useState(true);
  const [error, setError]       = useState(null);
  // Set after the first successful poll: from then on a drone without a stream is offline
  const [fetched, setFetched]   = useState(false);
  const mountedRef               = useRef(true);

  const fetchStreams = useCallback(async () => {
    try {
      const { data } = await api.get("/ingest-server/streams/active");
      if (!mountedRef.current) return;

      // Normalise — endpoint may return { drones: [...], count: N }
      const raw = Array.isArray(data) ? data : (data?.drones ?? []);
      const rxAt = Date.now();                       // browser clock, for isStreamRecent
      setStreams(raw.map((s) => ({ ...s, _rx_at: rxAt })));
      setFetched(true);
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
      // Offline until the next poll says otherwise (A5)
      setStreams(prev => prev.map(s => {
        if (s.drone_id === msg.drone_id) {
          return { ...s, status: "offline", _offline: true };
        }
        return s;
      }));
    } else if (["stream_purged", "mission_drained"].includes(msg.type || msg.event) && !msg.drone_still_active) {
      // The stream is gone (A5): drop it now instead of waiting for the next poll
      setStreams(prev => prev.filter(s => s.drone_id !== msg.drone_id));
    }
  });

  /** O(1) lookup map: drone_id → stream object */
  const streamMap = new Map(streams.map((s) => [s.drone_id, s]));

  /** Is a given drone ID currently streaming live frames? */
  const droneIsLive = useCallback(
    (id) => {
      const s = streamMap.get(id);
      const frames = s?.frame_count ?? s?.frames_received ?? 0;
      return !!s && isStreamRecent(s) && s.status !== "offline" && frames > 0;
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
          const st = resolveDroneStatus(stream, "offline");
          if (st === "offline") continue;
          combined.push({
            id:            stream.drone_id,
            status:        st,
            battery_pct:   stream.battery_pct ?? null,
            lat:           stream.latitude  ?? null,
            lng:           stream.longitude ?? null,
            altitude_m:    stream.altitude_m ?? null,
            mission_id:    stream.mission_id ?? null,
            mission_name:  stream.mission_name ?? null,
            operator_name: stream.operator ?? null,
            jurisdiction:  stream.location ?? null,
            messages_ready: stream.messages_ready ?? 0,
            // No signal reading or start time from ingest: leave unknown (UI shows "—")
            signal_pct:    stream.signal_pct ?? null,
            started_at:    stream.started_at ?? stream.mission_started_at ?? null,
            last_analytics: null,
          });
        }
      }

      // Enrich DB records with the freshest ingest data
      const enriched = combined.map((drone) => {
        const stream = streamMap.get(drone.id);
        if (!stream) {
          // No ingest stream: the drone is offline, whatever its DB row still says (A5: a
          // drained/purged drone kept "live"/"draining" and got a blank grid tile). Until
          // the first poll answered, the DB status stands.
          const status = fetched
            ? "offline"
            : (drone.status === "live" && !drone.mission_id ? "offline" : (drone.status || "offline"));
          return {
            ...drone,
            messages_ready: status === "offline" ? 0 : (drone.messages_ready ?? 0),
            status,
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
          signal_pct:    stream.signal_pct     ?? drone.signal_pct,
          started_at:    stream.started_at     ?? stream.mission_started_at ?? drone.started_at,
          last_seen:     stream.last_seen,
          messages_ready: stream.messages_ready ?? drone.messages_ready ?? 0,
          frame_count:   stream.frame_count    ?? stream.frames_received ?? 0,
          status:        resolveDroneStatus(stream, drone.status),
        };
      });

      // Sort: active live streams ALWAYS first → draining → degraded → offline; within tier: by id
      const statusOrder = { live: 0, draining: 1, degraded: 2, offline: 3 };
      enriched.sort((a, b) => {
        const aLiveStream = streamMap.has(a.id) && isStreamRecent(streamMap.get(a.id));
        const bLiveStream = streamMap.has(b.id) && isStreamRecent(streamMap.get(b.id));
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
    [streams, fetched]
  );

  const isGridDroneCb = useCallback(
    (drone) => isGridDrone(drone, streamMap),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [streams]
  );

  return { streams, streamMap, droneIsLive, mergeWithDrones, isGridDrone: isGridDroneCb, loading, error, fetched };
}

