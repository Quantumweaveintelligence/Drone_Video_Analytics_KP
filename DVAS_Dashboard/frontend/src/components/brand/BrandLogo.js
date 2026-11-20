import React from "react";

/**
 * SmartQEye logos. Each comes in a light-background version (navy) and a dark-background
 * version (white + lighter blue); `theme="auto"` follows the app theme (Tailwind `dark` class),
 * "light" / "dark" force one, for surfaces whose background never changes (e.g. the login page).
 */
const SRC = {
  wordmark: ["/brand/smartqeye-wordmark.png", "/brand/smartqeye-wordmark-dark.png"],
  mark: ["/brand/smartqeye-mark.png", "/brand/smartqeye-mark-dark.png"],
};

export function SmartQEyeLogo({ variant = "wordmark", theme = "auto", className = "", alt = "SmartQEye" }) {
  const [light, dark] = SRC[variant];
  if (theme === "light") return <img src={light} alt={alt} className={className} draggable={false} />;
  if (theme === "dark") return <img src={dark} alt={alt} className={className} draggable={false} />;
  return (
    <>
      <img src={light} alt={alt} className={`${className} dark:hidden`} draggable={false} />
      <img src={dark} alt={alt} className={`${className} hidden dark:block`} draggable={false} />
    </>
  );
}

export function QuantumWeaveLogo({ className = "" }) {
  return (
    <img src="/brand/quantumweave-logo.png" alt="QuantumWeave Intelligence" className={className} draggable={false} />
  );
}
