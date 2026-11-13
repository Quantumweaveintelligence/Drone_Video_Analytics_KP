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
