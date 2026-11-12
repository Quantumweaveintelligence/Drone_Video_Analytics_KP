import React from "react";
import PersonTrackingAnalytics from "@/components/PersonTrackingAnalytics";
import { useNavigate } from "react-router-dom";
import { useCommand } from "@/context/CommandContext";
import { useRequestedDrone } from "@/hooks/useRequestedDrone";

// P1/P9/P10: the component gets the selected drone, the user (role-gated tabs) and a way to
// open other command pages (e.g. Detection Models).
export default function PersonPage() {
  const { user, setSelectedDrone } = useCommand();
  const selectedDrone = useRequestedDrone();   // R8: ?drone= from the Multi-Drone View
  const navigate = useNavigate();
  // Only a drone the officer picked; otherwise the component selects a live stream itself
  const activeId = selectedDrone || "";

  return (
    <div className="w-full">
      <PersonTrackingAnalytics droneId={activeId} user={user} onSelectDrone={setSelectedDrone} onNavigateTab={(tab) => navigate(`/command/${tab}`)} />
    </div>
  );
}
