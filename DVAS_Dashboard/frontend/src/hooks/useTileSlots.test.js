import { assignTiles } from "./useTileSlots";

const d = (id, status = "live") => ({ id, status });

test("drones take the lowest free tiles, live first then by id", () => {
  expect(assignTiles({}, [d("B"), d("A", "draining"), d("C")], 20)).toEqual({ B: 0, C: 1, A: 2 });
});

test("a drone keeps its tile when others leave or join", () => {
  let a = assignTiles({}, [d("A"), d("B")], 20);          // A → 0, B → 1
  a = assignTiles(a, [d("B")], 20);                        // A gone: tile 0 free, B stays on 1
  expect(a).toEqual({ B: 1 });
  a = assignTiles(a, [d("B"), d("C")], 20);                // C takes the free tile 0
  expect(a).toEqual({ B: 1, C: 0 });
  a = assignTiles(a, [d("B", "draining"), d("C")], 20);    // a status change does not move it
  expect(a).toEqual({ B: 1, C: 0 });
});

test("more drones than tiles get extra tiles", () => {
  const drones = Array.from({ length: 22 }, (_, i) => d(`D${String(i).padStart(2, "0")}`));
  const a = assignTiles({}, drones, 20);
  expect(new Set(Object.values(a)).size).toBe(22);
  expect(Math.max(...Object.values(a))).toBe(21);
});
