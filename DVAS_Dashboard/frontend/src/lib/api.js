import axios from "axios";

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL || "";
export const API = `${BACKEND_URL}/api`;

export const api = axios.create({
  baseURL: API,
  withCredentials: true,
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
        await api.post("/auth/refresh");
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
