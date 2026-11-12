/**
 * analyticsServices.js
 * ───────────────────────────────────────────────────────────────
 * The analytics services as the Detection Models page lists them, and the command page that
 * shows each one's annotated feed (v2 plan R8, Multi-Drone View services panel).
 *
 * `key` is the Detection Models key (AVAILABLE_MODELS in backend/routers/infra.py); `service`
 * is the analyzer that runs it, the name /api/analytics/drone-states reports states under.
 */

export const ANALYTICS_SERVICES = [
  { key: "person",    service: "person",    label: "Person Detection",       page: "person",    family: "sky" },
  { key: "face",      service: "person",    label: "Face Recognition",       page: "person",    family: "amber" },
  { key: "vehicle",   service: "vehicle",   label: "Vehicle Tracking",       page: "traffic",   view: "vehicle", family: "blue" },
  { key: "plate",     service: "anpr",      label: "Number Plate (ANPR)",    page: "traffic",   view: "anpr",    family: "violet" },
  { key: "crowd",     service: "crowd",     label: "Crowd Density",          page: "crowd",     family: "emerald" },
  { key: "intrusion", service: "intrusion", label: "GeoTrax Intrusion",      page: "intrusion", family: "rose" },
  { key: "vessel",    service: "vessel",    label: "Maritime Vessels",       page: "traffic",   view: "vessel",  family: "cyan" },
  { key: "anomaly",   service: "anomaly",   label: "Anomaly & Suspicious",   page: "anomaly",   family: "orange" },
];

/** Command route of a service's page, opened on one drone: /command/<page>?drone=<id>[&view=…] */
export function serviceRoute(svc, droneId) {
  const q = new URLSearchParams({ drone: droneId });
  if (svc.view) q.set("view", svc.view);
  return `/command/${svc.page}?${q.toString()}`;
}

/**
 * One entry per service for a drone: enabled (selected in Detection Models), the label from the
 * models list when it has one, and the drone's measured state for that service:
 *   off       not selected in Detection Models
 *   starting  selected, no measurement for this drone yet
 *   live / degraded / shedding   from drone-states (R6.4)
 * `models` is the GET /api/models answer (or null while it loads); `droneState` is
 * drone-states[drone] (or undefined).
 */
export function serviceEntries(models, droneState) {
  const active = new Set(models?.active || []);
  const labels = Object.fromEntries((models?.available || []).map((m) => [m.key, m.label]));
  const measured = droneState?.services || {};
  return ANALYTICS_SERVICES.map((svc) => {
    const enabled = active.has(svc.key);
    const m = measured[svc.service];
    return {
      ...svc,
      label: labels[svc.key] || svc.label,
      enabled,
      state: !enabled ? "off" : m?.state || "starting",
      fps: enabled && typeof m?.fps === "number" ? m.fps : null,
      behind_s: enabled && typeof m?.behind_s === "number" ? m.behind_s : null,
    };
  });
}
