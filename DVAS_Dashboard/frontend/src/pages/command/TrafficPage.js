import React, { useEffect, useState } from "react";
import TrafficAnalytics from "@/components/TrafficAnalytics";
import VesselAnalytics from "@/components/VesselAnalytics";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useCommand } from "@/context/CommandContext";
import { useRequestedDrone } from "@/hooks/useRequestedDrone";
import { Car, Ship } from "lucide-react";

export default function TrafficPage() {
  const { user, drones } = useCommand();
  const navigate = useNavigate();
  // R8: the Multi-Drone View links here with ?drone=<id>&view=vehicle|anpr|vessel
  const selectedDrone = useRequestedDrone();
  const [params] = useSearchParams();
  const view = params.get("view") || "";
  const [activeTab, setActiveTab] = useState(view === "vessel" ? "vessel" : "vehicle");
  useEffect(() => {
    if (view) setActiveTab(view === "vessel" ? "vessel" : "vehicle");
  }, [view]);
  const activeId = selectedDrone || drones[0]?.id || "";
  const shared = { drones, droneId: activeId, user, onNavigateTab: (tab) => navigate(`/command/${tab}`) };

  return (
    <div className="w-full space-y-4">
      {/* Mode Switcher Tabs */}
      <div className="flex items-center justify-between border-b border-slate-700/60 pb-3">
        <div className="flex items-center gap-2 p-1 bg-slate-900/80 border border-slate-700/50 rounded-lg">
          <button
            onClick={() => setActiveTab("vehicle")}
            className={`flex items-center gap-2 px-4 py-2 rounded-md text-xs font-semibold tracking-wide transition-all ${
              activeTab === "vehicle"
                ? "bg-blue-600 text-white shadow-lg shadow-blue-900/40"
                : "text-slate-400 hover:text-slate-200 hover:bg-slate-800/60"
            }`}
          >
            <Car size={15} />
            <span>VEHICLE & ANPR</span>
          </button>
          <button
            onClick={() => setActiveTab("vessel")}
            className={`flex items-center gap-2 px-4 py-2 rounded-md text-xs font-semibold tracking-wide transition-all ${
              activeTab === "vessel"
                ? "bg-cyan-600 text-white shadow-lg shadow-cyan-900/40"
                : "text-slate-400 hover:text-slate-200 hover:bg-slate-800/60"
            }`}
          >
            <Ship size={15} />
            <span>MARITIME VESSEL TRACKING</span>
          </button>
        </div>

        {/* CMD-03: ingest status is shown (measured) in the top bar; no static badge here */}
      </div>

      {activeTab === "vehicle" ? (
        <TrafficAnalytics {...shared} initialFeedMode={view === "anpr" ? "anpr" : view === "vehicle" ? "vehicle" : ""} />
      ) : (
        <VesselAnalytics {...shared} />
      )}
    </div>
  );
}
