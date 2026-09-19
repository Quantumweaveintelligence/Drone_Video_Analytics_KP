import React from "react";
import AnomalyMonitor from "@/components/AnomalyMonitor";
import { useCommand } from "@/context/CommandContext";

export default function AnomalyPage() {
  const { selectedDrone, drones } = useCommand();
  const activeId = selectedDrone || "";

  return (
    <div className="w-full">
      <AnomalyMonitor droneId={activeId} drones={drones} />
    </div>
  );
}
