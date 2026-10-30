/**
 * deployment.js — build-time deployment target (RunPod plan Phase 2.7, finding F17).
 *
 * REACT_APP_DEPLOYMENT_TARGET is baked in at `npm run build` time:
 *   "cloud" — RunPod command dashboard: no /edge/* pages (the edge API only
 *             exists inside the DVAS Edge application on the laptops)
 *   "edge"  — DVAS Edge application: edge pages only
 *   "all"   — (default) local compose stack, every route as before
 */
export const TARGET = (process.env.REACT_APP_DEPLOYMENT_TARGET || "all").toLowerCase();
export const isCloud = TARGET === "cloud";
export const isEdge = TARGET === "edge";

/** Roles that may sign in to this build. */
export const ALLOWED_ROLES = isCloud
  ? ["control_room_officer", "senior_officer", "system_administrator"]
  : isEdge
    ? ["drone_operator"]
    : ["drone_operator", "control_room_officer", "senior_officer", "system_administrator"];

// Names used by the edge-exe-v1 branch (Phase 7.2: one shared deployment.js for both builds)
export const DEPLOYMENT_TARGET = TARGET;
export const isEdgeBuild = isEdge;
export const isCloudBuild = !isEdge;

/** True when a user with `role` may use this build. */
export function roleAllowedHere(role) {
  return ALLOWED_ROLES.includes(role);
}

/** Message shown when a signed-in role belongs to the other application. */
export function wrongAppMessage(role) {
  if (isCloud && role === "drone_operator") {
    return "Drone operators use the DVAS Edge application. Please sign in there.";
  }
  if (isEdge && role !== "drone_operator") {
    return "This is the DVAS Edge application for drone operators. Please use the DVAS command dashboard.";
  }
  return "";
}
