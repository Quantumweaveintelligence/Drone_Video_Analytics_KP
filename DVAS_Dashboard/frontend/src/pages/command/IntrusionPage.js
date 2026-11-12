import React from "react";
import IntrusionAnalytics from "@/components/IntrusionAnalytics";
import { useNavigate } from "react-router-dom";
import { useCommand } from "@/context/CommandContext";
import { useRequestedDrone } from "@/hooks/useRequestedDrone";

// P1/P9/P10: the component gets the selected drone, the user (role-gated tabs) and a way to
// open other command pages (e.g. Detection Models).
export default function IntrusionPage() {
  const { user, drones } = useCommand();
  const selectedDrone = useRequestedDrone();   // R8: ?drone= from the Multi-Drone View
  const navigate = useNavigate();
  const activeId = selectedDrone || drones[0]?.id || "";

  return (
    <div className="w-full">
      <IntrusionAnalytics droneId={activeId} user={user} onNavigateTab={(tab) => navigate(`/command/${tab}`)} />
    </div>
  );
}
