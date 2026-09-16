import React from "react";
import IntrusionAnalytics from "@/components/IntrusionAnalytics";
import { useCommand } from "@/context/CommandContext";

export default function IntrusionPage() {
  const { selectedDrone, drones } = useCommand();
  const activeId = selectedDrone || drones[0]?.id || "";

  return (
    <div className="w-full">
      <IntrusionAnalytics droneId={activeId} />
    </div>
  );
}
