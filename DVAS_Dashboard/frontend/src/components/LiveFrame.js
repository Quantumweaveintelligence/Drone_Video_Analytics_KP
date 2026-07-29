import React, { useState, useEffect, useRef } from "react";
import { API } from "@/lib/api";

export default function LiveFrame({ droneId, isOffline, isDegraded }) {
  const [imgSrc, setImgSrc] = useState(null);
  const [hasFrame, setHasFrame] = useState(false);
  const [feedDead, setFeedDead] = useState(false);
  const intervalRef = useRef(null);
  const failCountRef = useRef(0);
  const deadTimerRef = useRef(null);

  useEffect(() => {
    if (isOffline) return;

    setFeedDead(false);
    failCountRef.current = 0;

    const load = () => {
      const ts = Date.now();
      const url = `${API}/ingest-server/frame/${droneId}/latest?t=${ts}`;
      const img = new Image();
      
      img.onload = () => {
        setImgSrc(url);
        setHasFrame(true);
        setFeedDead(false);
        failCountRef.current = 0;
        
        // Reset the dead feed timer
        if (deadTimerRef.current) clearTimeout(deadTimerRef.current);
        deadTimerRef.current = setTimeout(() => {
          setFeedDead(true);
        }, 3000);
      };
      
      img.onerror = () => {
        failCountRef.current += 1;
        if (failCountRef.current > 6) {
          setFeedDead(true);
        }
      };
      
      img.src = url;
    };

    load();
    intervalRef.current = setInterval(load, 500);
    return () => {
      clearInterval(intervalRef.current);
      if (deadTimerRef.current) clearTimeout(deadTimerRef.current);
    };
  }, [droneId, isOffline]);

  if (isOffline || feedDead) {
    return (
      <>
        <div className="absolute inset-0 dvas-grid-bg opacity-40" />
        <div className="absolute inset-0 flex items-center justify-center">
          <div className="mono text-[10px] text-red-500 bg-black/60 px-2 py-1 rounded border border-red-500/30">
            FEED OFFLINE
          </div>
        </div>
      </>
    );
  }

  if (!hasFrame) {
    return (
      <>
        <div
          className="absolute inset-0 opacity-20"
          style={{
            background: isDegraded
              ? "radial-gradient(ellipse at 50% 50%, rgba(245,158,11,0.3) 0%, transparent 70%)"
              : "radial-gradient(ellipse at 50% 50%, rgba(20,184,166,0.15) 0%, transparent 70%)",
          }}
        />
        <div className="absolute bottom-6 left-0 right-0 flex justify-center">
          <div
            className="mono text-[8px] px-2 py-0.5 rounded"
            style={{ background: "rgba(6,14,26,0.65)", color: "rgba(148,163,184,0.5)" }}
          >
            AWAITING FEED…
          </div>
        </div>
      </>
    );
  }

  return (
    <>
      <img
        src={imgSrc}
        alt={`Live feed ${droneId}`}
        className="absolute inset-0 w-full h-full object-cover"
        style={{ opacity: 0.95 }}
      />
      <div className="absolute bottom-4 left-0 right-0 flex justify-center pointer-events-none">
        <div
          className="mono text-[8px] px-2 py-0.5 rounded flex items-center gap-1"
          style={{ background: "rgba(6,14,26,0.75)", color: isDegraded ? "#fbbf24" : "#2dd4bf" }}
        >
          <span
            className="inline-block w-1.5 h-1.5 rounded-full"
            style={{ background: isDegraded ? "#fbbf24" : "#2dd4bf", animation: "pulse 1s infinite" }}
          />
          LIVE FEED · {droneId}
        </div>
      </div>
    </>
  );
}
