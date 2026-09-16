import React from "react";
import TrafficAnalytics from "@/components/TrafficAnalytics";
import { useCommand } from "@/context/CommandContext";

export default function TrafficPage() {
  const { selectedDrone, drones } = useCommand();
  const activeId = selectedDrone || drones[0]?.id || "";

  return (
    <div className="w-full">
      <TrafficAnalytics droneId={activeId} />
    </div>
  );
}
