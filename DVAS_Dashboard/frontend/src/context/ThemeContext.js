import { createContext, useContext, useEffect, useState, useCallback } from "react";

const ThemeContext = createContext({ theme: "dark", toggle: () => {} });

const applyTheme = (t) => {
  const root = document.documentElement;
  root.classList.remove("light", "dark");
  root.classList.add(t);
  root.setAttribute("data-theme", t);
};

export function ThemeProvider({ children }) {
  const [theme, setTheme] = useState(() => {
    try {
      const stored = localStorage.getItem("dvas_theme");
      if (stored === "light" || stored === "dark") return stored;
    } catch (e) { /* noop */ }
    return "light";
  });

  useEffect(() => {
    applyTheme(theme);
    try { localStorage.setItem("dvas_theme", theme); } catch (e) { /* noop */ }
  }, [theme]);

  const toggle = useCallback(() => {
    setTheme((t) => (t === "dark" ? "light" : "dark"));
  }, []);

  return (
    <ThemeContext.Provider value={{ theme, toggle }}>
      {children}
    </ThemeContext.Provider>
  );
}

export const useTheme = () => useContext(ThemeContext);
