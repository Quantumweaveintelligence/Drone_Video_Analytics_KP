/**
 * useTileSlots.js
 * ───────────────────────────────────────────────────────────────
 * Fixed tile grid of the Multi-Drone View (v2 plan R7): always `count` tiles; a drone that
 * gets a tile takes the lowest free one and keeps it while it is in the list, so a tile never
 * jumps when another drone joins or leaves. A freed tile remembers the last drone that used it.
 * More drones than tiles: the extra drones get extra tiles after `count`.
 *
 * Returns [{ index, drone | null, lastId | null }] in tile order.
 */

import { useRef } from "react";

const STATUS_ORDER = { live: 0, degraded: 1, draining: 2, offline: 3 };

/** Pure step (tested): previous assignment {droneId: index} + drones → next assignment. */
export function assignTiles(prev, drones, count) {
  const ids = new Set(drones.map((d) => d.id));
  const next = {};
  const used = new Set();
  for (const [id, idx] of Object.entries(prev)) {
    if (ids.has(id)) {
      next[id] = idx;
      used.add(idx);
    }
  }
  // New drones in a stable order: live first, then by id
  const fresh = drones
    .filter((d) => !(d.id in next))
    .sort((a, b) => {
      const ao = STATUS_ORDER[a.status] ?? 4;
      const bo = STATUS_ORDER[b.status] ?? 4;
      return ao !== bo ? ao - bo : (a.id || "").localeCompare(b.id || "");
    });
  let free = 0;
  for (const d of fresh) {
    while (used.has(free)) free += 1;
    next[d.id] = free;
    used.add(free);
  }
  return next;
}

export function useTileSlots(drones, count = 20) {
  const assignRef = useRef({});
  const lastRef = useRef({});        // index → id of the last drone on that tile

  const assign = assignTiles(assignRef.current, drones, count);
  assignRef.current = assign;

  const byIndex = {};
  for (const d of drones) byIndex[assign[d.id]] = d;
  for (const [idx, d] of Object.entries(byIndex)) lastRef.current[idx] = d.id;

  const total = Math.max(count, ...Object.values(assign).map((i) => i + 1));
  return Array.from({ length: total }, (_, index) => ({
    index,
    drone: byIndex[index] || null,
    lastId: byIndex[index] ? null : lastRef.current[index] || null,
  }));
}
