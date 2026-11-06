import React from "react";
import CrowdAnalytics from "@/components/CrowdAnalytics";
import { useNavigate } from "react-router-dom";
import { useCommand } from "@/context/CommandContext";

// P1/P9/P10: the component gets the selected drone, the user (role-gated tabs) and a way to
// open other command pages (e.g. Detection Models).
export default function CrowdPage() {
  const { user, selectedDrone, setSelectedDrone } = useCommand();
  const navigate = useNavigate();
  // Only a drone the officer picked; otherwise the component selects a live stream itself
  const activeId = selectedDrone || "";

  return (
    <div className="w-full">
      <CrowdAnalytics droneId={activeId} user={user} onSelectDrone={setSelectedDrone} onNavigateTab={(tab) => navigate(`/command/${tab}`)} />
    </div>
  );
}
