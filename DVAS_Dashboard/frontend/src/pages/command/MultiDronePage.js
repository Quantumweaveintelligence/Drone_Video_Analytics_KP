import React from "react";
import { useNavigate } from "react-router-dom";
import MultiDroneView from "@/components/MultiDroneView";
import { useCommand } from "@/context/CommandContext";

// P8: live alerts and socket state come from the command context
export default function MultiDronePage() {
  const { user, mergedDrones, setSelectedDrone, alerts, connected } = useCommand();
  const navigate = useNavigate();

  return (
    <div className="w-full">
      <MultiDroneView
        drones={mergedDrones}
        alerts={alerts}
        connected={connected}
        user={user}
        onSelectDrone={setSelectedDrone}
        onNavigateTab={(tab) => navigate(`/command/${tab}`)}
      />
    </div>
  );
}
