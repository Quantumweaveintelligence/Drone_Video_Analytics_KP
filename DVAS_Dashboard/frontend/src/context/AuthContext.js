import { createContext, useContext, useEffect, useState } from "react";
import { api, authFetch } from "@/lib/api";

const AuthCtx = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null); // null = checking; false = anon
  const [ready, setReady] = useState(false);

  useEffect(() => {
    (async () => {
      try {
        const { data } = await api.get("/auth/me");
        setUser(data);
      } catch {
        setUser(false);
      } finally {
        setReady(true);
      }
    })();
  }, []);

  const login = async (email, password) => {
    const { data } = await api.post("/auth/login", { email, password });
    if (data?.access_token) {
      localStorage.setItem("dvas_token", data.access_token);
      localStorage.setItem("token", data.access_token);
    }
    setUser(data);
    return data;
  };

  const logout = async () => {
    try {
      // If there is an active mission on the edge, properly end it so frames stop sending
      const activeResp = await authFetch('/edge-api/api/edge/mission/active');
      if (activeResp.ok) {
        const activeData = await activeResp.json();
        if (activeData.active && activeData.mission_id) {
          await authFetch('/edge-api/api/edge/mission/end', { method: 'POST' });
          await authFetch(`/api/missions/${activeData.mission_id}/end`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' }
          });
          
          // Update the local storage mission log so it reflects the change when logging back in
          const savedLog = localStorage.getItem('dvas_mission_log');
          if (savedLog) {
            try {
              let logs = JSON.parse(savedLog);
              logs = logs.map(m => m.id === activeData.mission_id ? { ...m, status: 'completed', end: new Date().toLocaleTimeString('en-GB') } : m);
              localStorage.setItem('dvas_mission_log', JSON.stringify(logs));
            } catch(e) {}
          }
        }
      }
    } catch (e) {
      console.warn('Failed to clean up edge mission on logout', e);
    }

    try { await api.post("/auth/logout"); } catch {}
    localStorage.removeItem("dvas_token");
    localStorage.removeItem("token");
    setUser(false);
  };

  return (
    <AuthCtx.Provider value={{ user, ready, login, logout }}>
      {children}
    </AuthCtx.Provider>
  );
}

export const useAuth = () => useContext(AuthCtx);
