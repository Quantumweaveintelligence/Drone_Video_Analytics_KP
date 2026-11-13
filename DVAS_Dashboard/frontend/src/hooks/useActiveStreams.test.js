// jest has no "@/" alias here; the status rule needs neither module
jest.mock("@/lib/api", () => ({ api: {} }), { virtual: true });
jest.mock("@/lib/useDvasSocket", () => ({ useDvasSocket: () => ({}) }), { virtual: true });

import { resolveDroneStatus } from "./useActiveStreams";

const now = () => new Date().toISOString();

test("a drone streaming video with no analytics frames is live (R9)", () => {
  expect(resolveDroneStatus({ last_seen: now(), frame_count: 0, video_live: true })).toBe("live");
});

test("without an open uplink and without frames it stays offline", () => {
  expect(resolveDroneStatus({ last_seen: now(), frame_count: 0, video_live: false })).toBe("offline");
  expect(resolveDroneStatus({ last_seen: now(), frame_count: 0 })).toBe("offline");
});

test("frames still decide as before", () => {
  expect(resolveDroneStatus({ last_seen: now(), frame_count: 5 })).toBe("live");
  const old = new Date(Date.now() - 60000).toISOString();
  expect(resolveDroneStatus({ last_seen: old, frame_count: 0, video_live: true, messages_ready: 3 })).toBe("draining");
});

describe("recency is measured on one clock (R9)", () => {
  const { isStreamRecent } = require("./useActiveStreams");

  test("a viewer clock 15 s off does not flip a fresh drone to offline", () => {
    const now = Date.now();
    // ingest saw it 1 s ago (its clock); the poll came back 3 s ago (our clock);
    // last_seen read with our clock would look 18 s old, and 23 s on the next renders
    const s = { seen_age_s: 1, _rx_at: now - 3000, last_seen: new Date(now - 18000).toISOString(),
                frame_count: 0, video_live: true };
    expect(isStreamRecent(s, now)).toBe(true);
    expect(isStreamRecent(s, now + 4000)).toBe(true);       // later renders before the next poll
    expect(resolveDroneStatus(s)).toBe("live");
  });

  test("a stream ingest has not seen for a while is stale", () => {
    const now = Date.now();
    expect(isStreamRecent({ seen_age_s: 25, _rx_at: now }, now)).toBe(false);
    expect(isStreamRecent({ seen_age_s: 15, _rx_at: now - 6000 }, now)).toBe(false);
  });

  test("without seen_age_s it falls back to last_seen", () => {
    const now = Date.now();
    expect(isStreamRecent({ last_seen: new Date(now - 5000).toISOString() }, now)).toBe(true);
    expect(isStreamRecent({ last_seen: new Date(now - 60000).toISOString() }, now)).toBe(false);
  });
});
