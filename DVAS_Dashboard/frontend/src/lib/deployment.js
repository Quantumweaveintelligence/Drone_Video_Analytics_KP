/**
 * deployment.js — Build-target switch for DVAS frontend
 * =====================================================
 * Detects whether this build is for the field operator (edge .exe) or the
 * full control-room dashboard.
 *
 * Build with:
 *   REACT_APP_DEPLOYMENT_TARGET=edge  → edge-only build (11 edge pages + login)
 *   REACT_APP_DEPLOYMENT_TARGET=cloud → full dashboard (default)
 *   (no value / empty)                → full dashboard
 */

export const DEPLOYMENT_TARGET = process.env.REACT_APP_DEPLOYMENT_TARGET || "cloud";

/** True when running inside the edge .exe operator app */
export const isEdgeBuild = DEPLOYMENT_TARGET === "edge";

/** True when running the full control-room dashboard (RunPod / server) */
export const isCloudBuild = !isEdgeBuild;

/** Roles allowed to log in on this build */
export const ALLOWED_ROLES = isEdgeBuild
  ? ["drone_operator"]
  : ["drone_operator", "control_room_officer", "senior_officer", "system_administrator"];
