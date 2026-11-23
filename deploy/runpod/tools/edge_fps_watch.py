#!/usr/bin/env python3
"""edge_fps_watch.py — second by second, is each live drone's video arriving at its full frame
rate, and if not, where is it lost: on the edge, on the network, or on the pod?

Run on the pod while drones stream (it only reads; it changes nothing):
    /opt/venvs/ai/bin/python /opt/dvas/deploy/tools/edge_fps_watch.py --seconds 300 --out /root/diag-MMDD/run1

For every live mission (`mission:video:<sid>`, status receiving) it reads the recording's per-frame
meta (seg-*.meta.jsonl: the edge's seq, capture_ts, pts_ms, plus rx_ts = when the pod received it)
and the TS bytes, and for each second of arrival computes:
  rx_fps     frames received by the pod in that second
  cap_fps    the camera's rate: 1 / median spacing of those frames' capture_ts
  pts_fps    the encoder's rate: 1 / median spacing of their pts_ms
  gaps       frame numbers (seq) the edge assigned but never sent
  delay_s    median capture → pod receive (needs the laptop's clock in NTP sync); growth_s is the
             same minus its lowest value in the run, which does not depend on the clocks
  kbps       TS bytes received
  pub_fps    frames the pod's decoder published to the analytics (mission:video pub_fps)
  loop_ms    the ingest event loop's worst stall in the last 5 s (ingest:loop_lag)
A second is LOW when rx_fps < --low (default 80 %) × the drone's announced fps. Its cause:
  EDGE-DROP     gaps > 0: the edge numbered frames and dropped them before sending (its queue)
  EDGE-SOURCE   no gaps, but cap_fps or pts_fps is low too: the camera / encoder on the laptop
                made fewer frames
  UPLINK        frames captured at full rate arrive late: growth_s rising (network can't carry it)
  POD-INGEST    the ingest event loop stalled (loop_ms > 500): the pod did not read the socket
  BURST         none of the above: frames arrive in bursts (jitter), the average is fine
Writes <out>/edge_fps_<drone>.csv (one row per second) and <out>/edge_fps_summary.txt, and prints
a live line every 5 s.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import statistics as st
import sys
import time
from collections import defaultdict

try:
    import redis
except ImportError:
    sys.exit("run with /opt/venvs/ai/bin/python (needs the redis package)")

REC = os.getenv("DVAS_RECORD_DIR", "/workspace/dvas/recordings")
CAUSES = ("EDGE-DROP", "EDGE-SOURCE", "UPLINK", "POD-INGEST", "BURST")


def s(v) -> str:
    return v.decode() if isinstance(v, bytes) else ("" if v is None else str(v))


def f(v, d=0.0) -> float:
    try:
        return float(s(v))
    except ValueError:
        return d


class Mission:
    """Follows one mission's recording: meta lines and TS bytes since the last look."""

    def __init__(self, sid: str, h: dict):
        self.sid = sid
        self.drone = h.get("drone_id") or sid[:8]
        self.fps = f(h.get("fps"), 30.0) or 30.0
        self.size = f"{h.get('width', '?')}x{h.get('height', '?')}"
        self.offsets: dict = {}                     # meta file → bytes read
        self.ts_bytes = self._ts_bytes()
        self.last_seq = None
        self.rows: list = []
        self.min_delay = None
        self.buf: list = []                         # meta lines not yet assigned to a second
        self._skip_existing()

    def _dir(self) -> str:
        return os.path.join(REC, self.sid)

    def _ts_bytes(self) -> int:
        return sum(os.path.getsize(p) for p in glob.glob(os.path.join(self._dir(), "seg-*.ts")))

    def _skip_existing(self) -> None:
        for p in glob.glob(os.path.join(self._dir(), "seg-*.meta.jsonl")):
            self.offsets[p] = os.path.getsize(p)

    def read_new(self) -> None:
        for p in sorted(glob.glob(os.path.join(self._dir(), "seg-*.meta.jsonl"))):
            off = self.offsets.get(p, 0)
            try:
                with open(p, "rb") as fh:
                    fh.seek(off)
                    data = fh.read()
            except FileNotFoundError:
                continue
            end = data.rfind(b"\n") + 1               # whole lines only
            self.offsets[p] = off + end
            for line in data[:end].splitlines():
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if d.get("t") == "seg" or d.get("seq") is None or d.get("rx_ts") is None:
                    continue
                self.buf.append(d)

    def second(self, t_end: float, v: dict, loop_ms: float, low: float) -> dict:
        """Close the second ending at t_end (server clock) and classify it."""
        rows = [d for d in self.buf if float(d["rx_ts"]) < t_end]
        self.buf = [d for d in self.buf if float(d["rx_ts"]) >= t_end]
        rows.sort(key=lambda d: int(d["seq"]))
        n = len(rows)
        seqs = [int(d["seq"]) for d in rows]
        gaps = 0
        prev = self.last_seq
        for q in seqs:
            if prev is not None and q > prev + 1:
                gaps += q - prev - 1
            prev = q if prev is None else max(prev, q)
        if seqs:
            self.last_seq = max(seqs[-1], self.last_seq or 0)
        caps = sorted(float(d["capture_ts"]) for d in rows if d.get("capture_ts"))
        pts = sorted(float(d["pts_ms"]) for d in rows if d.get("pts_ms") is not None)
        dcap = [b - a for a, b in zip(caps, caps[1:]) if 0 < b - a < 2]
        dpts = [b - a for a, b in zip(pts, pts[1:]) if 0 < b - a < 2000]
        cap_fps = round(1 / st.median(dcap), 1) if dcap else None
        pts_fps = round(1000 / st.median(dpts), 1) if dpts else None
        delays = [float(d["rx_ts"]) - float(d["capture_ts"]) for d in rows if d.get("capture_ts")]
        delay = round(st.median(delays), 3) if delays else None
        if delay is not None:
            self.min_delay = delay if self.min_delay is None else min(self.min_delay, delay)
        growth = round(delay - self.min_delay, 3) if delay is not None and self.min_delay is not None else None
        tsb = self._ts_bytes()
        kbps = round((tsb - self.ts_bytes) * 8 / 1000)
        self.ts_bytes = tsb
        lowsec = n < low * self.fps
        cause = ""
        if lowsec:
            # gaps first: frames dropped after numbering also widen the capture spacing; a slow
            # camera or encoder numbers only what it makes, so it leaves no gaps
            if gaps > 0:
                cause = "EDGE-DROP"
            elif (cap_fps and cap_fps < low * self.fps) or (pts_fps and pts_fps < low * self.fps):
                cause = "EDGE-SOURCE"
            elif growth is not None and growth > 0.5:
                cause = "UPLINK"
            elif loop_ms > 500:
                cause = "POD-INGEST"
            else:
                cause = "BURST"
        row = {"t": round(t_end, 1), "rx_fps": n, "cap_fps": cap_fps, "pts_fps": pts_fps, "gaps": gaps,
               "delay_s": delay, "growth_s": growth, "kbps": kbps, "pub_fps": f(v.get("pub_fps"), 0.0),
               "behind_live_s": f(v.get("behind_live_s"), 0.0), "loop_ms": loop_ms, "low": int(lowsec),
               "cause": cause}
        self.rows.append(row)
        return row


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--seconds", type=int, default=300)
    ap.add_argument("--out", default=f"/root/diag-{time.strftime('%m%d')}/edge_fps")
    ap.add_argument("--low", type=float, default=0.8, help="a second is LOW below this share of the announced fps")
    ap.add_argument("--valkey", default="redis://127.0.0.1:6379/0")
    a = ap.parse_args()
    r = redis.Redis.from_url(a.valkey)
    os.makedirs(a.out, exist_ok=True)
    missions: dict = {}
    t_next = time.time() + 1.0
    t_stop = time.time() + a.seconds
    last_print = 0.0
    print(f"watching live drones for {a.seconds} s → {a.out}  (LOW = under {a.low:.0%} of the announced fps)")
    while time.time() < t_stop:
        time.sleep(max(0.0, t_next - time.time()))
        t_end, t_next = t_next, t_next + 1.0
        for k in r.scan_iter("mission:video:*", count=200):
            sid = s(k).split("mission:video:", 1)[1]
            h = {s(x): s(y) for x, y in r.hgetall(k).items()}
            if h.get("status") == "receiving" and sid not in missions and os.path.isdir(os.path.join(REC, sid)):
                missions[sid] = Mission(sid, h)
                print(f"  + {missions[sid].drone}: {missions[sid].size} @ {missions[sid].fps:g} fps announced")
        lag = r.get("ingest:loop_lag")
        try:
            loop_ms = float(json.loads(lag)["max_ms"]) if lag else 0.0
        except (ValueError, KeyError, TypeError):
            loop_ms = 0.0
        for sid, m in missions.items():
            m.read_new()
            v = {s(x): s(y) for x, y in r.hgetall(f"mission:video:{sid}").items()}
            # the meta of a frame can land up to ~0.5 s after its bytes: close the second before last
            m.second(t_end - 1.0, v, loop_ms, a.low)
        if time.time() - last_print >= 5 and missions:
            last_print = time.time()
            parts = []
            for m in missions.values():
                w = m.rows[-5:]
                rx = sum(x["rx_fps"] for x in w) / max(1, len(w))
                lows = [x["cause"] for x in w if x["low"]]
                parts.append(f"{m.drone} rx {rx:4.1f}/{m.fps:g}" + (f" LOW×{len(lows)} {max(set(lows), key=lows.count)}" if lows else ""))
            print(time.strftime("%H:%M:%S"), " | ".join(parts), flush=True)

    lines = [f"edge_fps_watch — {time.strftime('%Y-%m-%d %H:%M')}, {a.seconds} s, LOW = rx under {a.low:.0%} of the announced fps", ""]
    for m in missions.values():
        rows = m.rows[2:-1]                                 # the first and last seconds are partial
        path = os.path.join(a.out, f"edge_fps_{m.drone}.csv")
        with open(path, "w") as fh:
            keys = list(rows[0].keys()) if rows else []
            fh.write(",".join(keys) + "\n")
            for x in rows:
                fh.write(",".join("" if x[k] is None else str(x[k]) for k in keys) + "\n")
        if not rows:
            lines.append(f"== {m.drone}: no data")
            continue
        low = [x for x in rows if x["low"]]
        by = defaultdict(int)
        for x in low:
            by[x["cause"]] += 1
        longest, cur = 0, 0
        for x in rows:
            cur = cur + 1 if x["low"] else 0
            longest = max(longest, cur)
        med = lambda k: st.median([x[k] for x in rows if x[k] is not None]) if any(x[k] is not None for x in rows) else None  # noqa: E731
        lines += [f"== {m.drone}  {m.size} @ {m.fps:g} fps announced, {len(rows)} s",
                  f"   received      median {med('rx_fps')} fps, mean {sum(x['rx_fps'] for x in rows) / len(rows):.1f} fps, {med('kbps')} kbit/s median",
                  f"   camera        median {med('cap_fps')} fps (capture_ts)   encoder median {med('pts_fps')} fps (pts)",
                  f"   gaps          {sum(x['gaps'] for x in rows)} frames numbered but never sent",
                  f"   delay         median {med('delay_s')} s capture→pod (clock-dependent); worst growth {max((x['growth_s'] or 0) for x in rows):.2f} s",
                  f"   pod decoder   median {med('pub_fps')} fps published to analytics; ingest loop worst {max(x['loop_ms'] for x in rows):.0f} ms",
                  f"   LOW seconds   {len(low)} of {len(rows)} ({100 * len(low) / len(rows):.0f} %), longest run {longest} s",
                  "   by cause      " + (", ".join(f"{c} {by[c]}" for c in CAUSES if by[c]) or "none"),
                  ""]
    lines += ["Causes: EDGE-SOURCE camera/encoder on the laptop made fewer frames; EDGE-DROP the edge dropped",
              "numbered frames before sending; UPLINK frames arrive late (network); POD-INGEST the pod's ingest",
              "loop stalled; BURST frames came in bursts (jitter), average fine."]
    with open(os.path.join(a.out, "edge_fps_summary.txt"), "w") as fh:
        fh.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"saved: {a.out}/edge_fps_summary.txt and edge_fps_<drone>.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
