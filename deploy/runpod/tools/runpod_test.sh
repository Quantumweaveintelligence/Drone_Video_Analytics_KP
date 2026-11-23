#!/usr/bin/env bash
# runpod_test.sh — helpers for the RunPod test guide. Load once per terminal:
#     source /opt/dvas/deploy/tools/runpod_test.sh
# Then:
#   rt_check                    pod, image, settings, services, memory → $RT_DIR/check.txt
#   rt_engines on|off [names]   switch analytics engines (all when no names): crowd person vehicle anpr intrusion vessel anomaly
#   rt_flush                    clear frames and TEST recordings between runs (no mission may be live)
#   rt_watch <label> [seconds]  edge frame-rate watch (edge_fps_watch.py) → $RT_DIR/<label>/
#   rt_snap <label>             diagnostics snapshot (stress_diag.sh + shed log + frame ring) → $RT_DIR/<label>/
#   rt_restart_test [engine]    stop an engine 30 s, start it, show that its shedding restarted clean
#   rt_pack                     everything in one .tgz to send back
RT_DIR="/root/diag-$(date +%m%d)"
RT_SV="supervisorctl -c /etc/dvas/supervisord.conf"
RT_VC="valkey-cli -h 127.0.0.1"
RT_PY=/opt/venvs/ai/bin/python
RT_T=/opt/dvas/deploy/tools
declare -A RT_PORT=([crowd]=8101 [person]=8102 [vehicle]=8103 [anpr]=8104 [intrusion]=8105 [vessel]=8106 [anomaly]=8107)
mkdir -p "$RT_DIR"
set -a; . /workspace/dvas/secrets/dvas.env; set +a          # ANALYTICS_SERVICE_KEY for the tools

rt_check() {
  {
    echo "== $(date -Is)  image commit: ${DVAS_GIT_SHA:-unknown}"
    local p; p=$(pgrep -f "uvicorn intrusion.main:app" | head -1)
    echo "== shedding settings seen by the services (empty = defaults):"
    [ -n "$p" ] && tr '\0' '\n' < "/proc/$p/environ" | grep -E "^DVAS_(SHED|FRAME_RING|CUDA_MPS|THREADS)" || echo "   (intrusion not running)"
    echo "== services"; $RT_SV status | awk '{print "   " $1, $2}'
    echo "== shedding thresholds per service (from the logs)"
    grep -h "shedding: threshold" /var/log/dvas/{person,vehicle,anpr,intrusion,anomaly}.log 2>/dev/null | tail -5 | sed 's/^/   /'
    echo "== frame ring"; grep -h "frame ring" /var/log/dvas/video-decoder.log 2>/dev/null | tail -3 | sed 's/^/   /'
    echo "== memory, CPU, GPU, disk"
    df -h /dev/shm | sed 's/^/   /'; free -g | sed 's/^/   /'
    echo "   vCPUs visible: $(nproc)"; nvidia-smi -L | sed 's/^/   /'
    /opt/dvas/deploy/bin/dvas-ctl disk 2>&1 | sed 's/^/   /'
  } 2>&1 | tee "$RT_DIR/check.txt"
}

rt_engines() {
  local what=$1; shift
  local names=("$@"); [ ${#names[@]} -eq 0 ] && names=(crowd person vehicle anpr intrusion vessel anomaly)
  local path; [ "$what" = on ] && path=start || path=stop
  for n in "${names[@]}"; do
    printf "   %-9s %s: " "$n" "$path"
    curl -s -m 30 -o /dev/null -w "%{http_code}\n" -H "X-Internal-Service-Key: $ANALYTICS_SERVICE_KEY" \
      "http://127.0.0.1:${RT_PORT[$n]}/stream/$path"
  done
}

rt_flush() {
  if [ -n "$($RT_VC --scan --pattern 'mission:video:*' | while read -r k; do $RT_VC HGET "$k" status; done | grep -x receiving)" ]; then
    echo "!! a drone is still streaming — end every mission on the edge first"; return 1
  fi
  $RT_SV stop 'analytics:*' video-decoder
  for k in $($RT_VC --scan --pattern 'mission:frames:*') $($RT_VC --scan --pattern 'drone:frames:*') \
           $($RT_VC --scan --pattern 'mission:video:*'); do $RT_VC DEL "$k" >/dev/null; done
  rm -rf /workspace/dvas/recordings/*
  $RT_SV restart ingest; $RT_SV start video-decoder 'analytics:*'
  echo "   waiting 60 s for the models to load …"; sleep 60
  /opt/dvas/deploy/bin/dvas-ctl lag; $RT_SV status | grep -E "analytics|video-decoder" | awk '{print "   " $1, $2}'
}

rt_watch() {
  local label=${1:?usage: rt_watch <label> [seconds]} secs=${2:-300}
  mkdir -p "$RT_DIR/$label"
  $RT_PY "$RT_T/edge_fps_watch.py" --seconds "$secs" --out "$RT_DIR/$label"
}

rt_snap() {
  local label=${1:?usage: rt_snap <label>}
  local D="$RT_DIR/$label"; mkdir -p "$D"
  bash "$RT_T/stress_diag.sh" "$label" >/dev/null 2>&1
  $RT_PY - > "$D/services_now.txt" <<'PYEOF'
import json, redis
r = redis.Redis(host="127.0.0.1")
for s in ("person", "vehicle", "anpr", "intrusion", "anomaly", "crowd", "vessel"):
    raw = r.get(f"dvas:svc_stats:{s}")
    d = json.loads(raw) if raw else {}
    ring = d.get("frame_ring") or {}
    print(f"{s:9s} frame ring hits {ring.get('hits')} misses {ring.get('misses')}")
    for k, v in (d.get("drones") or {}).items():
        st = v.get("stages") or {}
        print(f"   {k}: {v.get('fps')} fps of {v.get('input_fps')} in, level {v.get('shed_level')}, "
              f"every {v.get('cadence')} frame, shed {v.get('frames_shed')} since start, "
              f"threshold {v.get('shed_threshold_s')} s, decode {st.get('decode')} ms, total {st.get('total')} ms")
    if not raw:
        print("   (not running)")
PYEOF
  $RT_PY - > "$D/shed_audit.txt" <<'PYEOF'
import redis, datetime as dt, time
r = redis.Redis(host="127.0.0.1")
since = int((time.time() - 3600) * 1000)
for eid, f in r.xrange("dvas:analytics_shed", min=f"{since}-0", max="+"):
    f = {k.decode(): v.decode() for k, v in f.items()}
    t = dt.datetime.fromtimestamp(int(eid.decode().split("-")[0]) / 1000).strftime("%H:%M:%S")
    if f.get("reason") == "level":
        print(t, f.get("service"), f.get("drone_id"), "LEVEL", f.get("from_level"), "->", f.get("to_level"),
              "every", f.get("cadence"), "behind", f.get("behind_s"))
    else:
        print(t, f.get("service"), f.get("drone_id"), f.get("reason"), "frames", f.get("frames"),
              "seq", f.get("from_seq"), "-", f.get("to_seq"), "behind", f.get("behind_s"))
PYEOF
  grep -hE "shedding level|stall:|stream restart|new mission stream" \
    /var/log/dvas/{person,vehicle,anpr,intrusion,anomaly}.log 2>/dev/null | tail -300 > "$D/shed_levels.txt"
  ls -la /dev/shm/dvas-ring/ > "$D/frame_ring_files.txt" 2>&1; df -h /dev/shm >> "$D/frame_ring_files.txt"
  echo "saved in $D:"; ls "$D"; echo; cat "$D/services_now.txt"
}

rt_restart_test() {
  local n=${1:-person}
  echo "== stopping $n for 30 s, then starting it (a drone must be streaming)"
  rt_engines off "$n"; sleep 30; rt_engines on "$n"; sleep 25
  grep -hE "stream restart|shedding level|stall:" "/var/log/dvas/$n.log" | tail -6
  $RT_VC GET "dvas:svc_stats:$n" | $RT_PY -c 'import json,sys; d=json.loads(sys.stdin.read() or "{}"); print({k: (v.get("shed_level"), v.get("cadence"), v.get("frames_shed")) for k, v in (d.get("drones") or {}).items()}, "(level, every Nth, frames shed since the restart)")'
}

rt_pack() {
  local out="/root/smartqeye-test-$(date +%m%d-%H%M).tgz"
  tar czf "$out" "$RT_DIR" /workspace/dvas/bench/stress-* 2>/dev/null
  ls -lh "$out"
}

echo "runpod_test helpers loaded — results go to $RT_DIR (rt_check, rt_engines, rt_flush, rt_watch, rt_snap, rt_restart_test, rt_pack)"
