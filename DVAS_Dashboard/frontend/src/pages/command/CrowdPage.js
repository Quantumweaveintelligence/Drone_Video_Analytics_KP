import React from "react";
import CrowdAnalytics from "@/components/CrowdAnalytics";
import { useCommand } from "@/context/CommandContext";

export default function CrowdPage() {
  const { selectedDrone, drones } = useCommand();
  const activeId = selectedDrone || drones[0]?.id || "";

  return (
    <div className="w-full">
      <CrowdAnalytics droneId={activeId} />
    </div>
  );
}
