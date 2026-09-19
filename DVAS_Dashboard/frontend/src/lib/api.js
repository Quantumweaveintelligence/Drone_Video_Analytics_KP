import axios from "axios";

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL || "";
export const API = `${BACKEND_URL}/api`;

export const api = axios.create({
  baseURL: API,
  withCredentials: true,
});

export const getAuthToken = () => {
  return localStorage.getItem("dvas_token") || localStorage.getItem("token") || "";
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
  const token = getAuthToken();
  const tokenParam = token ? `&token=${encodeURIComponent(token)}` : "";
  return `/api/minio/image?bucket=${encodeURIComponent(bucket)}&key=${encodeURIComponent(cleanKey)}${tokenParam}`;
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
      const refreshRes = await api.post("/auth/refresh");
      const newToken = refreshRes?.data?.access_token;
      if (newToken) {
        localStorage.setItem("dvas_token", newToken);
      }
      const retryToken = newToken || getAuthToken();
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
      if (window.location.pathname !== "/login") {
        window.location.href = "/login";
      }
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
        const refreshRes = await api.post("/auth/refresh");
        if (refreshRes?.data?.access_token) {
          localStorage.setItem("dvas_token", refreshRes.data.access_token);
          originalRequest.headers.Authorization = `Bearer ${refreshRes.data.access_token}`;
        }
        return api(originalRequest);
      } catch (refreshError) {
        if (window.location.pathname !== "/login") {
          window.location.href = "/login";
        }
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
