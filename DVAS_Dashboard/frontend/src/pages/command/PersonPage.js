import React from "react";
import PersonTrackingAnalytics from "@/components/PersonTrackingAnalytics";
import { useCommand } from "@/context/CommandContext";

export default function PersonPage() {
  const { selectedDrone, drones } = useCommand();
  const activeId = selectedDrone || drones[0]?.id || "";

  return (
    <div className="w-full">
      <PersonTrackingAnalytics droneId={activeId} />
    </div>
  );
}
