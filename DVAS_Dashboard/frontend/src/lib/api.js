import axios from "axios";

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
export const API = `${BACKEND_URL}/api`;

export const api = axios.create({
  baseURL: API,
  withCredentials: true,
});

// Attach bearer token if present in localStorage (helps in cross-domain preview)
api.interceptors.request.use((config) => {
  const t = localStorage.getItem("dvas_token");
  if (t) config.headers.Authorization = `Bearer ${t}`;
  return config;
});

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
