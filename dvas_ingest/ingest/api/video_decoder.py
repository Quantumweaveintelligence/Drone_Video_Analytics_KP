# -*- coding: utf-8 -*-
"""
DVAS video decoder — recording → analytics frames (RunPod v2 plan R1 item 4).

A separate process (supervisord `video-decoder`). For every mission with a video recording
(video_store) it decodes the TS segments in order, keeps DVAS_ANALYTICS_FPS frames per
second, attaches each frame's telemetry from the segment's meta.jsonl, and XADDs it to the
mission's frame stream with the same fields as /ingest/frame — the analyzers cannot tell
the difference, except `source=h264` and `rx_ts_ms`.

No frame is lost when the analytics fall behind (decision 2026-11-10):
  * Flow control: before each XADD the decoder reads how many frames the active analyzer
    groups have not finished (lag + pending). At DVAS_VIDEO_WINDOW_FRAMES it waits. The
    backlog stays on disk in the recording, not in Valkey; MAXLEN is 4 × the window, a
    safety bound that flow control never reaches.
  * A group counts only while its consumers read (QUEUE_READER_IDLE_S) and its service
    publishes stats (dvas:svc_stats:<service>, TTL 10 s): a switched-off service does not
    hold the others back.
  * Restart-safe: the resume point (segment, connection, PTS of the last published frame)
    is saved with every frame; after a restart decoding resumes one segment before it
    (segments start at a keyframe) and frames up to that PTS are skipped.
  * Frames carry the decoder's own contiguous frame_seq in presentation order; the edge's
    seq (decode order, with gaps from the 30 → 20 fps sampling) is kept as edge_seq.
  * decoder_done=1 only after the last frame of an ended mission was published; ingest's
    drain waits for it, so a mission stream is never purged before the analytics saw it.

Also runs the recording retention (DVAS_RECORD_RETENTION_DAYS, default 7).
"""
from __future__ import annotations

import asyncio
import bisect
import json
import logging
import math
import os
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Dict, Optional

import queue_manager as qm
import video_store as vs

log = logging.getLogger("video-decoder")


def _num_env(name: str, default: float, lo: float, hi: float) -> float:
    try:
        return max(lo, min(hi, float(os.getenv(name, str(default)))))
    except ValueError:
        return default


ANALYTICS_FPS = _num_env("DVAS_ANALYTICS_FPS", 20.0, 1.0, 60.0)
MAX_WIDTH = int(_num_env("DVAS_ANALYTICS_MAX_WIDTH", 0, 0, 7680))      # 0 = source resolution
JPEG_Q = int(_num_env("DVAS_ANALYTICS_JPEG_Q", 90, 50, 100))
WINDOW = int(_num_env("DVAS_VIDEO_WINDOW_FRAMES", 100, 10, 2000))
RETENTION_DAYS = _num_env("DVAS_RECORD_RETENTION_DAYS", 7, 0.1, 3650)
KEEP_RECORDINGS = os.getenv("DVAS_RECORD_VIDEO", "1").lower() in ("1", "true", "yes")
SCAN_S = 1.0
PROGRESS_EVERY = 20
POLL_S = 0.05
RETENTION_EVERY_S = 3600.0
SVC_STATS_KEY = "dvas:svc_stats:{service}"


def _s(v) -> str:
    return v.decode("utf-8", errors="replace") if isinstance(v, bytes) else ("" if v is None else str(v))


def _f(v, default: float = 0.0) -> float:
    try:
        return float(_s(v))
    except (TypeError, ValueError):
        return default


def seg_conn(sid: str, idx: int) -> Optional[int]:
    """Connection number from a segment's first meta line (None while not written yet)."""
    try:
        with open(vs.meta_path(sid, idx), "rb") as f:
            first = f.readline()
        doc = json.loads(first)
        return int(doc["conn"]) if doc.get("t") == "seg" else None
    except (FileNotFoundError, ValueError, KeyError, TypeError):
        return None


class TailReader:
    """File-like reader over the segments of one connection ("run"), waiting for new data
    while the edge is still streaming. EOF at the end of the run: the next segment belongs
    to another connection, or the mission will receive nothing more."""

    def __init__(self, sid: str, start_seg: int, conn: int, final: Callable[[], bool],
                 stop: threading.Event):
        self.sid, self.seg, self.conn = sid, start_seg, conn
        self._final, self._stop = final, stop
        self._f = None
        self.next_run_seg: Optional[int] = None      # set when the run ended at another connection
        self._last_final_check = 0.0
        self._is_final = False

    def _open(self) -> bool:
        if self._f is None:
            try:
                self._f = open(vs.seg_path(self.sid, self.seg), "rb")
            except FileNotFoundError:
                return False
        return True

    def read(self, n: int = 65536) -> bytes:
        n = 65536 if n is None or n < 0 else n
        while not self._stop.is_set():
            if self._open():
                b = self._f.read(n)
                if b:
                    return b
            nxt = self.seg + 1
            if os.path.exists(vs.seg_path(self.sid, nxt)):
                c = seg_conn(self.sid, nxt)
                if c is None:
                    time.sleep(POLL_S)               # being created: its first line follows
                    continue
                if self._f is not None:
                    b = self._f.read(n)              # the rest of a segment closed meanwhile
                    if b:
                        return b
                if c != self.conn:
                    self.next_run_seg = nxt
                    return b""
                if self._f is not None:
                    self._f.close()
                self._f, self.seg = None, nxt
                continue
            now = time.monotonic()
            if now - self._last_final_check >= 0.5:
                self._last_final_check = now
                self._is_final = self._final()
            if self._is_final:
                if self._f is not None:
                    b = self._f.read(n)
                    if b:
                        return b
                return b""
            time.sleep(POLL_S)
        return b""

    def close(self) -> None:
        if self._f is not None:
            self._f.close()
            self._f = None


class MetaIndex:
    """Frame meta of one run, read incrementally from the segments' meta.jsonl, matched to
    decoded frames by PTS (offset learned at the first keyframe)."""

    def __init__(self, sid: str, start_seg: int, conn: int, fps: float):
        self.sid, self.conn = sid, conn
        self._seg = start_seg
        self._pos: Dict[int, int] = {}
        self._pts: list = []                  # sorted pts_ms
        self._by_pts: Dict[float, dict] = {}
        self.offset: Optional[float] = None
        self._matched: Optional[float] = None
        self.tol = max(4.0, 500.0 / max(1.0, fps))
        self.missing = 0

    def _load(self) -> None:
        for idx in (self._seg, self._seg + 1, self._seg + 2):
            p = vs.meta_path(self.sid, idx)
            try:
                with open(p, "rb") as f:
                    f.seek(self._pos.get(idx, 0))
                    data = f.read()
            except FileNotFoundError:
                break
            cut = data.rfind(b"\n") + 1               # only whole lines
            self._pos[idx] = self._pos.get(idx, 0) + cut
            for raw in data[:cut].splitlines():
                try:
                    m = json.loads(raw)
                except ValueError:
                    continue
                if m.get("t") == "seg":
                    if int(m.get("conn", -1)) != self.conn:
                        return                       # the next run begins here
                    self._seg = max(self._seg, int(m.get("seg", idx)))
                    continue
                pts = m.get("pts_ms")
                if pts is None or pts in self._by_pts:
                    continue                         # a resent frame keeps its first meta
                self._by_pts[pts] = m
                bisect.insort(self._pts, pts)

    def _nearest(self, target: float) -> Optional[dict]:
        i = bisect.bisect_left(self._pts, target)
        best = None
        for j in (i - 1, i):
            if 0 <= j < len(self._pts) and abs(self._pts[j] - target) <= self.tol:
                if best is None or abs(self._pts[j] - target) < abs(best - target):
                    best = self._pts[j]
        return self._by_pts.get(best) if best is not None else None

    def _learn(self, t_ms: float, after: Optional[float] = None) -> None:
        """Pair a decoded keyframe with the first key meta (after `after`): PTS offset."""
        for p in self._pts:
            if self._by_pts[p].get("key") and (after is None or p > after):
                self.offset = t_ms - p
                return

    def _lookup(self, t_ms: float) -> Optional[dict]:
        return None if self.offset is None else self._nearest(t_ms - self.offset)

    def match(self, t_ms: float, is_key: bool) -> Optional[dict]:
        if not self._pts:
            self._load()
        if self.offset is None and is_key:
            self._learn(t_ms)
        m = self._lookup(t_ms)
        if m is None:
            self._load()                             # meta written after our last read
            if self.offset is None and is_key:
                self._learn(t_ms)
            m = self._lookup(t_ms)
        if m is None and is_key and self.offset is not None:
            old = self.offset                         # PTS jumped: re-sync on this keyframe
            self._learn(t_ms, after=self._matched)
            m = self._lookup(t_ms)
            if m is None:
                self.offset = old
        if m is None:
            self.missing += 1
            return None
        self._matched = m["pts_ms"]
        self._trim(m["pts_ms"])
        return m

    def _trim(self, upto: float) -> None:
        cut = bisect.bisect_left(self._pts, upto - 5000.0)
        if cut > 256:
            for p in self._pts[:cut]:
                self._by_pts.pop(p, None)
            del self._pts[:cut]


class MissionDecoder(threading.Thread):
    """Decodes one mission's recording, run (connection) by run, into its frame stream.

    Frames are published in presentation order with the decoder's own contiguous
    `frame_seq` (1, 2, 3 …, persisted as analytics_seq), so the analyzers' ordering and gap
    counting stay exact although the edge's `seq` follows the packets' decode order (B-frames)
    and 30 → 20 fps sampling leaves gaps in it. The edge's number is kept as `edge_seq`.

    Resume point (saved every PROGRESS_EVERY frames): decoder_seg, decoder_conn, decoder_pts
    (last published frame's PTS in that connection), analytics_seq, decoder_floor / edge_max
    (frames a later connection re-sends carry an edge seq ≤ the floor and are skipped).
    """

    def __init__(self, sid: str, loop: asyncio.AbstractEventLoop, qman: "qm.QueueManager",
                 sync_redis):
        super().__init__(name=f"decoder-{sid}", daemon=True)
        self.sid = sid
        self.key = qm.mission_stream_key(sid)
        self.vkey = vs.video_key(sid)
        self.loop, self.qm, self.r = loop, qman, sync_redis
        self.stop = threading.Event()
        self.published = 0
        self.aseq = 0                     # last published frame_seq (contiguous)
        self.edge_max = 0                 # highest edge seq published
        self.failed = False
        self._pub_times: list = []
        self._resume_conn = 0
        self._resume_pts: Optional[float] = None
        self._resume_floor = 0

    # ── state ────────────────────────────────────────────────────────────────
    def _h(self) -> dict:
        return {_s(k): _s(v) for k, v in (self.r.hgetall(self.vkey) or {}).items()}

    def _mission(self) -> dict:
        return {_s(k): _s(v) for k, v in (self.r.hgetall(qm.mission_index_key(self.sid)) or {}).items()}

    def _purged(self) -> bool:
        idx = self._mission()
        return not idx or bool(idx.get("purged_at"))

    def _final(self) -> bool:
        """No more data will arrive for this mission."""
        st = _s(self.r.hget(self.vkey, "status"))
        if st == "ended":
            return True
        if st == "receiving":
            return False
        idx = self._mission()
        return not idx or idx.get("status") != "live" or bool(idx.get("purged_at"))

    # ── main ─────────────────────────────────────────────────────────────────
    def run(self) -> None:
        try:
            self._run()
        except Exception as exc:                      # logged; the scanner retries it later
            self.failed = True
            log.exception("decoder %s failed: %s", self.sid, exc)
            try:
                self.r.hset(self.vkey, "decoder_error", str(exc)[:300])
            except Exception:
                pass

    def _run(self) -> None:
        h = self._h()
        self.aseq = int(_f(h.get("analytics_seq"), 0))
        self.edge_max = int(_f(h.get("edge_max"), 0))
        self._resume_conn = int(_f(h.get("decoder_conn"), 0))
        self._resume_pts = _f(h.get("decoder_pts"), 0.0) if h.get("decoder_pts") else None
        self._resume_floor = int(_f(h.get("decoder_floor"), 0))
        drone_id, mission_id = h.get("drone_id") or "", h.get("mission_id") or self.sid
        fps_src = _f(h.get("fps"), 30.0) or 30.0
        segs = vs.segments(self.sid)
        if not segs:
            if self._final():
                self._finish("no recording")
            return
        start = max(int(_f(h.get("decoder_seg"), 0)) or segs[0], segs[0])
        log.info("decoder %s: start at segment %d (conn %d, after frame %d)",
                 self.sid, start, self._resume_conn, self.aseq)
        while not self.stop.is_set():
            if self._purged():
                self._finish("mission purged")
                return
            conn = seg_conn(self.sid, start)
            if conn is None:
                if not os.path.exists(vs.seg_path(self.sid, start)) and self._final():
                    self._finish("end of recording")
                    return
                time.sleep(POLL_S * 4)
                continue
            nxt = self._decode_run(start, conn, drone_id, mission_id, fps_src)
            if nxt is None:                           # the mission's last run is decoded
                if self._final() and not self.stop.is_set():
                    self._finish("end of recording")
                return
            start = nxt

    def _decode_run(self, start: int, conn: int, drone_id: str, mission_id: str,
                    fps_src: float) -> Optional[int]:
        import av  # noqa: PLC0415
        reader = TailReader(self.sid, start, conn, self._final, self.stop)
        metas = MetaIndex(self.sid, start, conn, fps_src)
        container = None
        bucket_last = None
        t0 = None
        failures = 0
        # what of this run was already published (resume) and which edge seqs are re-sends
        if conn < self._resume_conn:
            skip_upto, floor = float("inf"), 0
        elif conn == self._resume_conn:
            skip_upto, floor = self._resume_pts, self._resume_floor
        else:
            skip_upto, floor = None, self.edge_max
        self._run_conn, self._run_floor = conn, floor
        try:
            try:
                container = av.open(reader, format="mpegts", mode="r",
                                    options={"analyzeduration": "2000000", "probesize": "2000000"})
                stream = container.streams.video[0]
            except Exception as exc:
                if self._final() or reader.next_run_seg is not None:
                    log.error("decoder %s: run at segment %d has no decodable video (%s) — skipped",
                              self.sid, start, exc)
                    return reader.next_run_seg
                raise
            stream.thread_type = "AUTO"
            tb = float(stream.time_base) if stream.time_base else 1 / 90000
            for packet in container.demux(stream):
                if self.stop.is_set():
                    break
                try:
                    decoded = packet.decode()
                except av.error.InvalidDataError as exc:   # damaged data: go on with what decodes
                    failures += 1
                    log.debug("decoder %s: %s", self.sid, exc)
                    continue
                for frame in decoded:                 # presentation order
                    if frame.pts is None:
                        continue
                    t_ms = frame.pts * tb * 1000.0
                    m = metas.match(t_ms, bool(frame.key_frame))
                    if skip_upto is not None and t_ms <= skip_upto + 0.5:
                        continue                      # published before a restart
                    if m is not None and floor and int(m["seq"]) <= floor:
                        continue                      # re-sent by the edge after a reconnect
                    if t0 is None:
                        t0 = t_ms
                    bucket = math.floor((t_ms - t0) / 1000.0 * ANALYTICS_FPS + 1e-6)
                    if bucket_last is not None and bucket <= bucket_last:
                        continue                      # not sampled (source faster than DVAS_ANALYTICS_FPS)
                    bucket_last = bucket
                    self._publish(frame, m, t_ms, drone_id, mission_id, reader.seg)
            if failures:
                log.warning("decoder %s: %d damaged packets skipped in run at segment %d",
                            self.sid, failures, start)
            if metas.missing:
                log.warning("decoder %s: %d frames without meta in run at segment %d",
                            self.sid, metas.missing, start)
        finally:
            if container is not None:
                try:
                    container.close()
                except Exception:
                    pass
            reader.close()
        if self.stop.is_set():
            return start
        return reader.next_run_seg

    def _publish(self, frame, m: Optional[dict], t_ms: float, drone_id: str,
                 mission_id: str, seg: int) -> None:
        import cv2  # noqa: PLC0415
        img = frame.to_ndarray(format="bgr24")
        h, w = img.shape[:2]
        if MAX_WIDTH and w > MAX_WIDTH:
            img = cv2.resize(img, (MAX_WIDTH, max(1, round(h * MAX_WIDTH / w))), interpolation=cv2.INTER_AREA)
            h, w = img.shape[:2]
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, JPEG_Q])
        if not ok:
            log.error("decoder %s: JPEG encode failed at %.0f ms", self.sid, t_ms)
            return
        m = m or {}
        tel = m.get("tel") or {}
        rx = float(m.get("rx_ts") or time.time())
        cap = float(m.get("capture_ts") or rx)
        meta = {"drone_id": drone_id, "mission_id": mission_id,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(cap)) + f".{int(cap * 1000) % 1000:03d}Z",
                "frame_sequence": self.aseq + 1, "change_score": m.get("change_score", 0.0),
                "capture_ts": cap, "rx_ts_ms": int(rx * 1000), "source": "h264", "width": w, "height": h,
                "pts_ms": round(t_ms, 3), "edge_seq": m.get("seq"), **tel}
        if not m:
            meta["meta_missing"] = True
        fut = asyncio.run_coroutine_threadsafe(
            self._publish_async(buf.tobytes(), meta, t_ms, int(m.get("seq") or 0), seg, rx), self.loop)
        fut.result()

    async def _window_used(self) -> int:
        try:
            unread = await self.qm.unread_frames(self.key)
        except Exception:
            return 0
        used = 0
        for group, n in unread.items():
            svc = group[:-6] if group.endswith("-group") else group
            if not await self.qm._client.exists(SVC_STATS_KEY.format(service=svc)):
                continue                              # service not running: does not hold the feed
            used = max(used, n)
        return used

    async def _publish_async(self, jpeg: bytes, meta: dict, t_ms: float, edge_seq: int,
                             seg: int, rx: float) -> None:
        while not self.stop.is_set():
            if await self._window_used() < WINDOW:
                break
            await asyncio.sleep(POLL_S)
        if self.stop.is_set():
            raise RuntimeError("stopped")
        entry = await self.qm.publish_frame(meta["drone_id"], jpeg, meta, stream_key=self.key,
                                            maxlen=WINDOW * 4)
        self.aseq += 1
        self.edge_max = max(self.edge_max, edge_seq)
        self.published += 1
        now = time.monotonic()
        self._pub_times.append(now)
        self._pub_times = [t for t in self._pub_times if now - t <= 5.0]
        fields = {"last_entry_id": _s(entry), "analytics_seq": self.aseq, "edge_max": self.edge_max,
                  "decoder_conn": self._run_conn, "decoder_pts": repr(round(t_ms, 3)),
                  "decoder_floor": self._run_floor}
        if self.published % PROGRESS_EVERY == 0:
            rx_last = _f(await self.qm._client.hget(self.vkey, "rx_ts_last"), rx)
            fields.update({"decoder_seg": max(1, seg - 1),
                           "pub_fps": round(len(self._pub_times) / 5.0, 2),
                           "behind_live_s": round(max(0.0, rx_last - rx), 2)})
        else:
            fields["decoder_seg"] = max(1, seg - 1)     # the demuxer reads ahead: one segment back
        await self.qm._client.hset(self.vkey, mapping=fields)

    def _finish(self, why: str) -> None:
        self.r.hset(self.vkey, mapping={"decoder_done": 1, "analytics_seq": self.aseq,
                                        "behind_live_s": 0, "pub_fps": 0, "decoder_error": ""})
        log.info("decoder %s: done (%s), %d frames published this run, frame_seq %d",
                 self.sid, why, self.published, self.aseq)


class DecoderService:
    def __init__(self, qman: Optional["qm.QueueManager"] = None):
        self.qm = qman or qm.get_queue_manager()
        self.running: Dict[str, MissionDecoder] = {}
        self._retry_at: Dict[str, float] = {}
        import redis  # noqa: PLC0415
        self.r = redis.Redis.from_url(qm.REDIS_URL)
        self._last_retention = 0.0

    async def run(self) -> None:
        await self.qm.connect()
        loop = asyncio.get_running_loop()
        log.info("video-decoder ready: %.0f fps, width cap %s, window %d frames, recordings %s",
                 ANALYTICS_FPS, MAX_WIDTH or "source", WINDOW, vs.RECORD_ROOT)
        while True:
            try:
                await self.scan(loop)
                if time.monotonic() - self._last_retention >= RETENTION_EVERY_S:
                    self._last_retention = time.monotonic()
                    await asyncio.to_thread(self.retention)
            except Exception as exc:
                log.warning("scan failed: %s", exc)
            await asyncio.sleep(SCAN_S)

    async def scan(self, loop) -> None:
        now = time.monotonic()
        for sid, t in list(self.running.items()):
            if not t.is_alive():
                self.running.pop(sid, None)
                if t.failed:
                    self._retry_at[sid] = now + 10.0
        cursor = 0
        while True:
            cursor, keys = await self.qm._client.scan(cursor=cursor, match="mission:video:*", count=200)
            for k in keys:
                sid = _s(k).split("mission:video:", 1)[1]
                if sid in self.running or self._retry_at.get(sid, 0.0) > now:
                    continue
                if _s(await self.qm._client.hget(_s(k), "decoder_done")) == "1":
                    continue
                t = MissionDecoder(sid, loop, self.qm, self.r)
                self.running[sid] = t
                t.start()
            if not cursor:
                break

    def retention(self) -> None:
        """Delete recordings older than RETENTION_DAYS (or, with DVAS_RECORD_VIDEO=0, as soon
        as they are analysed) — never one still being decoded, a live mission's, or one with a
        HOLD file (legal hold)."""
        root = vs.RECORD_ROOT
        if not root.is_dir():
            return
        now = time.time()
        for d in root.iterdir():
            if not d.is_dir():
                continue
            sid = d.name
            try:
                age = now - max(p.stat().st_mtime for p in d.iterdir()) if any(d.iterdir()) else now
                h = {_s(k): _s(v) for k, v in (self.r.hgetall(vs.video_key(sid)) or {}).items()}
                idx = {_s(k): _s(v) for k, v in (self.r.hgetall(qm.mission_index_key(sid)) or {}).items()}
                if (d / "HOLD").exists() or idx.get("status") in ("live", "draining"):
                    continue
                if h and h.get("decoder_done") != "1":
                    continue
                if age < RETENTION_DAYS * 86400 and KEEP_RECORDINGS:
                    continue
                shutil.rmtree(d)
                self.r.delete(vs.video_key(sid))
                log.info("recording %s deleted (%.1f days old%s)", sid, age / 86400,
                         "" if KEEP_RECORDINGS else ", DVAS_RECORD_VIDEO=0")
            except Exception as exc:
                log.warning("retention check failed for %s: %s", sid, exc)


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), stream=sys.stdout,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    asyncio.run(DecoderService().run())


if __name__ == "__main__":
    main()
