import React from "react";
import MultiDroneView from "@/components/MultiDroneView";
import { useCommand } from "@/context/CommandContext";

export default function MultiDronePage() {
  const { mergedDrones, setSelectedDrone } = useCommand();

  return (
    <div className="w-full">
      <MultiDroneView drones={mergedDrones} onSelectDrone={setSelectedDrone} />
    </div>
  );
}
