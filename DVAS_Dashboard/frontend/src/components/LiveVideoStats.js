/**
 * LiveVideoStats — the live player's numbers on a video tile (useLiveVideo stats).
 * Shown only with the debug switch (localStorage dvas.liveDebug = "1", or ?livedebug=1).
 * fps low with dropped rising → the browser cannot decode it; kbps low and "behind" swinging
 * → the network; reconnects / seeks rising → the stream keeps breaking.
 */
export default function LiveVideoStats({ stats, sub }) {
  if (!stats) return null;
  return (
    <div className="absolute left-2 bottom-2 z-20 pointer-events-none mono text-[9px] leading-tight px-1.5 py-1 rounded bg-black/70 text-emerald-300">
      {stats.res}{sub ? " sub" : ""} · {stats.fps} fps · drop {stats.dropped}
      <br />
      {stats.kbps} kbps · behind {stats.behind}s · ×{stats.rate} · rc {stats.reconnects} · seek {stats.seeks}
    </div>
  );
}
