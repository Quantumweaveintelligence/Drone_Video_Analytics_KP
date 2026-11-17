#!/usr/bin/env bash
# stress_diag.sh <label> — snapshot after (or during) one stress run, saved to /root/diag-<MMDD>/<label>/
#
#   uplink.txt        per mission, what the edge sent: frames, fps by capture time and by PTS,
#                     seq gaps (frames the edge never sent), Mbit/s, capture → server delay
#   decoder_cost.txt  one recorded segment decoded the way video-decoder does it, per step (ms)
#   decoder_threads   per-thread CPU of video-decoder (a thread at ~100 % = one core is the limit)
#   svc_stats.json    every engine's published stats: fps, shed level, per-stage ms
#   disk / minio      volume and bucket sizes;  ingest_warnings / loop lag: event-loop stalls
set -u
L=${1:?usage: stress_diag.sh <label>}
D=/root/diag-$(date +%m%d)/$L
mkdir -p "$D"
V=/workspace/dvas
PY=/opt/venvs/ai/bin/python
VC="valkey-cli -h 127.0.0.1"
CTL=/opt/dvas/deploy/bin/dvas-ctl

$CTL disk > "$D/disk.txt" 2>&1
du -sh "$V"/minio/* > "$D/minio_buckets.txt" 2>&1
$CTL lag > "$D/lag.txt" 2>&1
for s in person vehicle anpr intrusion anomaly crowd vessel; do
  echo "== $s"; $VC GET "dvas:svc_stats:$s"; echo
done > "$D/svc_stats.json"
$VC GET ingest:loop_lag > "$D/ingest_loop_lag.txt"
for k in $($VC --scan --pattern 'mission:video:*'); do
  echo "== $k"; $VC HGETALL "$k" | paste - -
done > "$D/mission_video.txt"
P=$(pgrep -f 'python video_decoder.py' | head -1)
[ -n "$P" ] && top -H -b -n 3 -d 2 -p "$P" > "$D/decoder_threads.txt" 2>&1
top -b -n 1 -o %CPU | head -45 > "$D/top.txt"
nvidia-smi > "$D/nvidia-smi.txt" 2>&1
grep -hE 'event loop blocked|disk_full|bad_chunk|over_bitrate|video_refused|write.*slow' /var/log/dvas/ingest.log | tail -200 > "$D/ingest_warnings.txt"
tail -300 /var/log/dvas/video-decoder.log > "$D/video-decoder.log"
for s in person vehicle anpr intrusion anomaly crowd vessel; do tail -300 "/var/log/dvas/$s.log" > "$D/$s.log" 2>/dev/null; done

# What the edge sent, from the recording's per-frame meta (seq, capture_ts, pts_ms, rx_ts)
$PY - "$V/recordings" > "$D/uplink.txt" 2>&1 <<'PYEOF'
import glob, json, os, statistics as st, sys
root = sys.argv[1]
missions = sorted(glob.glob(f"{root}/*/"), key=os.path.getmtime)[-8:]
if not missions:
    print("no recordings")
for m in missions:
    rows = []
    for f in sorted(glob.glob(f"{m}seg-*.meta.jsonl")):
        for line in open(f):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("t") == "seg" or d.get("seq") is None:
                continue
            rows.append(d)
    if len(rows) < 10:
        continue
    seqs = [int(r["seq"]) for r in rows]
    cap = [float(r.get("capture_ts") or 0) for r in rows]
    rx = [float(r.get("rx_ts") or 0) for r in rows]
    pts = [float(r["pts_ms"]) for r in rows if r.get("pts_ms") is not None]
    span_cap = max(cap) - min(cap) or 1
    span_rx = max(rx) - min(rx) or 1
    gaps = sum(max(0, b - a - 1) for a, b in zip(seqs, seqs[1:]))
    dpts = [b - a for a, b in zip(pts, pts[1:]) if 0 < b - a < 2000]
    dcap = [b - a for a, b in zip(cap, cap[1:]) if 0 < b - a < 2]
    delay = [r - c for r, c in zip(rx, cap) if c]
    nbytes = sum(os.path.getsize(f) for f in glob.glob(f"{m}seg-*.ts"))
    q = lambda xs, p: sorted(xs)[min(len(xs) - 1, int(p * len(xs)))] if xs else float("nan")
    print(f"== {os.path.basename(m.rstrip('/'))}: {len(rows)} frames over {span_rx:.0f} s")
    print(f"   received        {len(rows) / span_rx:5.1f} fps   {nbytes * 8 / span_rx / 1e6:5.2f} Mbit/s")
    print(f"   by capture time {len(rows) / span_cap:5.1f} fps   capture interval median {1000 * st.median(dcap) if dcap else float('nan'):.0f} ms")
    print(f"   by PTS          {1000 / st.median(dpts) if dpts else float('nan'):5.1f} fps   (the encoder's frame spacing)")
    print(f"   seq gaps        {gaps} frames numbered but never sent ({100 * gaps / (len(rows) + gaps):.0f} %)")
    print(f"   capture→server  p50 {q(delay, .5):.2f} s  p95 {q(delay, .95):.2f} s  first {delay[0]:.2f} s  last {delay[-1]:.2f} s  (growing = the edge falls behind)")
PYEOF

# One recorded segment, decoded the way video-decoder does it: decode → BGR → JPEG
SEG=$(ls -t "$V"/recordings/*/seg-*.ts 2>/dev/null | sed -n 2p)
if [ -n "$SEG" ]; then
  $PY - "$SEG" > "$D/decoder_cost.txt" 2>&1 <<'PYEOF'
import sys, time, av, cv2
q = 90
try:
    sys.path.insert(0, "/opt/dvas/dvas_ingest/ingest/api")
    import video_decoder as vd
    q = getattr(vd, "JPEG_Q", q)
except Exception:
    pass
c = av.open(sys.argv[1]); s = c.streams.video[0]; s.thread_type = "AUTO"
t = {"decode": 0.0, "to_bgr": 0.0, "jpeg": 0.0}; n = 0; size = None
for p in c.demux(s):
    a = time.perf_counter(); fr = p.decode(); t["decode"] += time.perf_counter() - a
    for f in fr:
        a = time.perf_counter(); img = f.to_ndarray(format="bgr24"); t["to_bgr"] += time.perf_counter() - a
        a = time.perf_counter(); cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, q]); t["jpeg"] += time.perf_counter() - a
        n += 1; size = img.shape[1::-1]
tot = sum(t.values())
print(f"{sys.argv[1]}: {n} frames {size}, JPEG quality {q}")
for k, v in t.items():
    print(f"  {k:7s} {1000 * v / max(1, n):6.1f} ms/frame")
print(f"  total   {1000 * tot / max(1, n):6.1f} ms/frame -> at most {n / tot if tot else 0:.1f} fps per stream on one core")
PYEOF
fi
echo "saved in $D:"; ls "$D"
