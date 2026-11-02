import axios from "axios";
import { isEdge } from "@/lib/deployment";

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL || "";
export const API = `${BACKEND_URL}/api`;

export const api = axios.create({
  baseURL: API,
  withCredentials: true,
});

/**
 * Browser auth is the httpOnly dvas_access_token cookie only. The DVAS Edge application
 * (another origin) receives the token in the login body and keeps it in localStorage;
 * every other build returns "" so no token is ever put in headers or URLs.
 */
export const getAuthToken = () => {
  if (!isEdge) return "";
  return localStorage.getItem("dvas_token") || localStorage.getItem("token") || "";
};

const storeEdgeToken = (token) => {
  if (isEdge && token) localStorage.setItem("dvas_token", token);
};

// One refresh at a time: parallel 401s (polls, SSE, WebSocket) share the same request.
let refreshInFlight = null;
export const refreshSession = () => {
  if (!refreshInFlight) {
    refreshInFlight = api.post("/auth/refresh")
      .then((res) => { storeEdgeToken(res?.data?.access_token); return res; })
      .finally(() => { setTimeout(() => { refreshInFlight = null; }, 0); });
  }
  return refreshInFlight;
};

const goToLogin = () => {
  if (window.location.pathname !== "/login") window.location.href = "/login";
};

export const resolveMinioBucket = (key, explicitBucket = "") => {
  if (explicitBucket && explicitBucket.trim()) return explicitBucket.trim();
  if (!key) return "dvas-faces";
  const clean = key.replace(/^\/+/, "");
  if (clean.startsWith("vehicles/")) return "dvas-vehicles";
  if (clean.startsWith("plates/")) return "dvas-plates";
  if (clean.startsWith("vessels/")) return "dvas-vessels-maritime";
  if (clean.startsWith("intrusion/")) return "dvas-intrusion";
  if (clean.startsWith("heatmaps/")) return "dvas-heatmaps";
  if (clean.startsWith("watchlist/")) return "dvas-watchlist";
  if (clean.startsWith("evidence/")) return "dvas-evidence";
  if (clean.startsWith("anomaly/") || clean.startsWith("anomalies/") || clean.startsWith("alerts/")) return "dvas-anomaly";
  if (clean.startsWith("persons/") || clean.startsWith("faces/")) return "dvas-faces";
  return "dvas-faces";
};

export const resolveMinioImageUrl = (key, explicitBucket = "") => {
  if (!key) return "";
  if (key.startsWith("http://") || key.startsWith("https://")) return key;
  const cleanKey = key.replace(/^\/+/, "");
  const bucket = resolveMinioBucket(cleanKey, explicitBucket);
  // Auth comes from the httpOnly dvas_access_token cookie (same origin); no token in the URL.
  return `/api/minio/image?bucket=${encodeURIComponent(bucket)}&key=${encodeURIComponent(cleanKey)}`;
};

/**
 * Download a file through a single-use, 60-second signed link (evidence plan E5):
 * kind = "bundle" | "report" | "evidence_object". No token is put in the URL.
 */
export const downloadSigned = async (kind, id, role) => {
  const res = await api.post("/downloads", { kind, id, role });
  window.location.assign(`${BACKEND_URL}${res.data.url}`);
};

/** Absolute ws(s):// URL for an API path; the httpOnly cookie authenticates the handshake. */
export const wsUrl = (path) => {
  const base = API.startsWith("http")
    ? API.replace(/^http/, "ws")
    : `${window.location.protocol === "https:" ? "wss:" : "ws:"}//${window.location.host}${API}`;
  const token = getAuthToken();                 // edge build only
  const url = `${base}${path}`;
  return token ? `${url}${url.includes("?") ? "&" : "?"}token=${encodeURIComponent(token)}` : url;
};

/** Reconnect delay: 2 s, 4 s, 8 s … capped at 30 s, with jitter. */
export const backoffMs = (attempt) => Math.min(30000, 2000 * 2 ** attempt) * (0.8 + Math.random() * 0.4);

/** Fetch a file through the API (cookie auth) and save it under `filename`. */
export const downloadBlob = async (path, params = {}, filename = "download") => {
  const res = await api.get(path, { params, responseType: "blob" });
  const cd = res.headers?.["content-disposition"] || "";
  const m = cd.match(/filename\*?=(?:UTF-8'')?"?([^";]+)"?/i);
  const url = URL.createObjectURL(res.data);
  const a = document.createElement("a");
  a.href = url;
  a.download = m ? decodeURIComponent(m[1]) : filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
};

/** Human-readable message from an API error (FastAPI 422 details are arrays of objects). */
export const apiError = (err, fallback = "Request failed") => {
  const d = err?.response?.data?.detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d)) return d.map(x => x?.msg || JSON.stringify(x)).join("; ");
  if (d && typeof d === "object") return d.message || JSON.stringify(d);
  return err?.message || fallback;
};

export const getAuthHeaders = () => {
  const token = getAuthToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
};

export const authFetch = async (url, options = {}) => {
  const token = getAuthToken();
  const headers = {
    ...(options.headers || {}),
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };

  let response = await fetch(url, {
    ...options,
    headers,
    credentials: "include",
  });

  if (response.status === 401 && !url.includes("/auth/")) {
    try {
      await refreshSession();
      const retryToken = getAuthToken();
      const retryHeaders = {
        ...(options.headers || {}),
        ...(retryToken ? { Authorization: `Bearer ${retryToken}` } : {}),
      };
      response = await fetch(url, {
        ...options,
        headers: retryHeaders,
        credentials: "include",
      });
    } catch (e) {
      goToLogin();
    }
  }

  return response;
};

// Automatically attach Bearer token to all outgoing API requests
api.interceptors.request.use((config) => {
  const token = getAuthToken();
  if (token && !config.headers.Authorization) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// Auto-refresh token on 401 response and replay original request transparently
api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const originalRequest = error.config;
    if (
      error.response?.status === 401 &&
      !originalRequest._retry &&
      !originalRequest.url.includes("/auth/login") &&
      !originalRequest.url.includes("/auth/refresh")
    ) {
      originalRequest._retry = true;
      try {
        await refreshSession();
        const token = getAuthToken();
        if (token) originalRequest.headers.Authorization = `Bearer ${token}`;
        return api(originalRequest);
      } catch (refreshError) {
        goToLogin();
        return Promise.reject(refreshError);
      }
    }
    return Promise.reject(error);
  }
);

// 4-role model: field_supervisor → control_room_officer, supervisory_officer → senior_officer
export const ROLE_LABELS = {
  drone_operator: "Drone Operator",
  control_room_officer: "Control Room Officer",
  senior_officer: "Senior Officer",
  system_administrator: "System Administrator",
};

export const ROLE_HOME = {
  drone_operator: "/edge",
  control_room_officer: "/command",
  senior_officer: "/command",
  system_administrator: "/ingest",
};
