import React from "react";
import PersonTrackingAnalytics from "@/components/PersonTrackingAnalytics";
import { useNavigate } from "react-router-dom";
import { useCommand } from "@/context/CommandContext";

// P1/P9/P10: the component gets the selected drone, the user (role-gated tabs) and a way to
// open other command pages (e.g. Detection Models).
export default function PersonPage() {
  const { user, selectedDrone, drones } = useCommand();
  const navigate = useNavigate();
  const activeId = selectedDrone || drones[0]?.id || "";

  return (
    <div className="w-full">
      <PersonTrackingAnalytics droneId={activeId} user={user} onNavigateTab={(tab) => navigate(`/command/${tab}`)} />
    </div>
  );
}
