import React from "react";
import IntrusionAnalytics from "@/components/IntrusionAnalytics";
import { useNavigate } from "react-router-dom";
import { useCommand } from "@/context/CommandContext";

// P1/P9/P10: the component gets the selected drone, the user (role-gated tabs) and a way to
// open other command pages (e.g. Detection Models).
export default function IntrusionPage() {
  const { user, selectedDrone, drones } = useCommand();
  const navigate = useNavigate();
  const activeId = selectedDrone || drones[0]?.id || "";

  return (
    <div className="w-full">
      <IntrusionAnalytics droneId={activeId} user={user} onNavigateTab={(tab) => navigate(`/command/${tab}`)} />
    </div>
  );
}
