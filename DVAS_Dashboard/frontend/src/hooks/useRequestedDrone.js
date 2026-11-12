/**
 * useRequestedDrone.js
 * ───────────────────────────────────────────────────────────────
 * The drone a command page shows first. A link with ?drone=<id> (the Multi-Drone View services
 * panel, v2 plan R8) makes that drone the command context's selection, so the page opens on it
 * and still does after a reload; after that the page's own drone picker changes it as before.
 * Without ?drone= this is just the context's selected drone.
 */

import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useCommand } from "@/context/CommandContext";

export function useRequestedDrone() {
  const { selectedDrone, setSelectedDrone } = useCommand();
  const [params] = useSearchParams();
  const requested = params.get("drone") || "";
  // Until the context has the requested drone, answer with it, so the first render already uses it
  const [applied, setApplied] = useState(!requested);

  useEffect(() => {
    if (!requested) return;
    setSelectedDrone(requested);
    setApplied(true);
  }, [requested, setSelectedDrone]);

  return applied ? selectedDrone : requested;
}
