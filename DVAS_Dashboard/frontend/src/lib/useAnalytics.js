/**
 * useAnalytics.js
 * ─────────────────────────────────────────────────────────────────────────────
 * Shared React hooks for DVAS Analytics API integration.
 * Kerala Police | Drone Video Analytics System
 *
 * Exports:
 *   useAnalyticsFetch(path, intervalMs)  — polling hook
 *   useAnalyticsSSE(onEvent)             — SSE stream hook
 *   useAnalyticsSummary()                — gateway summary
 *   useVehicleTracks(droneId)            — live vehicle ledger
 *   usePlatesRecent(droneId)             — recent ANPR plates
 *   usePersonTracks(droneId)             — live person track table
 *   usePersonDedup()                     — deduplication stats
 *   usePersonIdentities(droneId)         — person gallery
 *   useFacesGallery(droneId)             — faces gallery
 *   useCrowdReadings(droneId)            — crowd density readings
 *   useCrowdTimeline(droneId)            — crowd 24h chart
 *   useTheftAlerts()                     — theft watchlist alerts
 *   useWatchlist()                       — watchlist management
 */

import { useState, useEffect, useRef, useCallback } from 'react';
import { api } from './api';

// ── Generic polling fetch hook ─────────────────────────────────────────────
export function useAnalyticsFetch(path, intervalMs = 5000, enabled = true) {
  const [data, setData]     = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError]   = useState(null);
  const mountedRef = useRef(true);

  const doFetch = useCallback(async () => {
    if (!enabled || !path) return;
    try {
      const res = await api.get(path);
      if (mountedRef.current) {
        setData(res.data);
        setError(null);
      }
    } catch (err) {
      if (mountedRef.current) {
        setError(err?.response?.data?.detail || err.message || 'Fetch error');
      }
    } finally {
      if (mountedRef.current) setLoading(false);
    }
  }, [path, enabled]);

  useEffect(() => {
    mountedRef.current = true;
    setLoading(true);
    doFetch();
    if (!intervalMs) return;
    const id = setInterval(doFetch, intervalMs);
    return () => {
      mountedRef.current = false;
      clearInterval(id);
    };
  }, [doFetch, intervalMs]);

  return { data, loading, error, refetch: doFetch };
}

// ── SSE stream hook ────────────────────────────────────────────────────────
export function useAnalyticsSSE(onEvent, droneId = '') {
  const [connected, setConnected] = useState(false);
  const [lastEvent, setLastEvent] = useState(null);
  const esRef = useRef(null);

  useEffect(() => {
    const token = localStorage.getItem('dvas_token');
    let url = '/api/analytics/events/stream';
    if (droneId) url += `?drone_id=${droneId}`;
    if (token) url += `${droneId ? '&' : '?'}token=${token}`;

    const connect = () => {
      if (esRef.current) esRef.current.close();
      const es = new EventSource(url, { withCredentials: true });
      esRef.current = es;

      es.onopen = () => setConnected(true);

      es.onmessage = (evt) => {
        try {
          const parsed = JSON.parse(evt.data);
          setLastEvent(parsed);
          if (onEvent) onEvent(parsed);
        } catch (_) {
          // non-JSON heartbeat — ignore
        }
      };

      es.onerror = () => {
        setConnected(false);
        es.close();
        // Reconnect after 5s
        setTimeout(connect, 5000);
      };
    };

    connect();
    return () => {
      if (esRef.current) esRef.current.close();
    };
  }, [droneId]); // eslint-disable-line react-hooks/exhaustive-deps

  return { connected, lastEvent };
}

// ── Analytics summary (gateway) ────────────────────────────────────────────
export function useAnalyticsSummary(intervalMs = 10000) {
  return useAnalyticsFetch('/analytics/summary', intervalMs);
}

// ── Crowd hooks ────────────────────────────────────────────────────────────
export function useCrowdReadings(droneId = '', intervalMs = 5000) {
  const path = droneId
    ? `/analytics/crowd/readings?drone_id=${droneId}&limit=50`
    : '/analytics/crowd/readings?limit=50';
  return useAnalyticsFetch(path, intervalMs);
}

export function useCrowdTimeline(droneId = '', hours = 24) {
  const path = droneId
    ? `/analytics/crowd/timeline?drone_id=${droneId}&hours=${hours}`
    : `/analytics/crowd/timeline?hours=${hours}`;
  return useAnalyticsFetch(path, 30000); // refresh every 30s
}

export function useCrowdHeatmap(droneId = '') {
  const path = droneId
    ? `/analytics/crowd/heatmap?drone_id=${droneId}`
    : '/analytics/crowd/heatmap';
  return useAnalyticsFetch(path, 15000);
}

// ── Person hooks ───────────────────────────────────────────────────────────
export function usePersonTracks(droneId = '', intervalMs = 5000) {
  const path = droneId
    ? `/analytics/person/tracks?drone_id=${droneId}&limit=100`
    : '/analytics/person/tracks?limit=100';
  return useAnalyticsFetch(path, intervalMs);
}

export function usePersonDedup(intervalMs = 15000) {
  return useAnalyticsFetch('/analytics/person/dedup', intervalMs);
}

export function usePersonIdentities(droneId = '', intervalMs = 10000) {
  const path = droneId
    ? `/analytics/person/identities?drone_id=${droneId}&limit=50`
    : '/analytics/person/identities?limit=50';
  return useAnalyticsFetch(path, intervalMs);
}

export function useFacesGallery(droneId = '', intervalMs = 10000) {
  const path = droneId
    ? `/analytics/person/gallery?drone_id=${droneId}&limit=60`
    : '/analytics/person/gallery?limit=60';
  return useAnalyticsFetch(path, intervalMs);
}

export function usePersonCount(droneId = '', intervalMs = 5000) {
  const path = droneId
    ? `/analytics/person/count?drone_id=${droneId}`
    : '/analytics/person/count';
  return useAnalyticsFetch(path, intervalMs);
}

// ── Vehicle hooks ──────────────────────────────────────────────────────────
export function useVehicleTracks(droneId = '', intervalMs = 5000) {
  const path = droneId
    ? `/analytics/vehicle/tracks?drone_id=${droneId}&limit=100`
    : '/analytics/vehicle/tracks?limit=100';
  return useAnalyticsFetch(path, intervalMs);
}

export function useVehicleRecent(droneId = '', intervalMs = 5000) {
  const path = droneId
    ? `/analytics/vehicle/recent?drone_id=${droneId}&limit=50`
    : '/analytics/vehicle/recent?limit=50';
  return useAnalyticsFetch(path, intervalMs);
}

export function useVehicleStats(intervalMs = 30000) {
  return useAnalyticsFetch('/analytics/vehicle/stats', intervalMs);
}

export function useVehicleByColour(intervalMs = 30000) {
  return useAnalyticsFetch('/analytics/vehicle/by-colour', intervalMs);
}

// ── ANPR / Plate hooks ─────────────────────────────────────────────────────
export function usePlatesRecent(droneId = '', intervalMs = 5000) {
  const path = droneId
    ? `/analytics/plates/recent?drone_id=${droneId}&limit=50`
    : '/analytics/plates/recent?limit=50';
  return useAnalyticsFetch(path, intervalMs);
}

export function useWatchlist(intervalMs = 30000) {
  const { data, loading, error, refetch } = useAnalyticsFetch('/analytics/watchlist', intervalMs);

  const addPlate = useCallback(async (plate, reason = '', vehicleType = '') => {
    await api.post('/analytics/watchlist', { plate, reason, vehicle_type: vehicleType });
    refetch();
  }, [refetch]);

  const removePlate = useCallback(async (plate) => {
    await api.delete(`/analytics/watchlist/${plate}`);
    refetch();
  }, [refetch]);

  return { data, loading, error, refetch, addPlate, removePlate };
}

export function useTheftAlerts(intervalMs = 10000) {
  return useAnalyticsFetch('/analytics/alerts/theft?limit=20', intervalMs);
}

export function useAnalyticsHealth(intervalMs = 15000) {
  return useAnalyticsFetch('/analytics/health', intervalMs);
}
