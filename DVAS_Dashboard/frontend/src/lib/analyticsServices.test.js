import { ANALYTICS_SERVICES, serviceEntries, serviceRoute } from "./analyticsServices";

const MODELS = {
  active: ["person", "plate", "crowd"],
  available: [{ key: "person", label: "Person Detection" }, { key: "plate", label: "Number-Plate (ANPR)" }],
};

test("every Detection Models service is listed; only the selected ones are enabled", () => {
  const e = serviceEntries(MODELS, undefined);
  expect(e.map((x) => x.key)).toEqual(["person", "face", "vehicle", "plate", "crowd", "intrusion", "vessel", "anomaly"]);
  expect(e.filter((x) => x.enabled).map((x) => x.key)).toEqual(["person", "plate", "crowd"]);
  expect(e.find((x) => x.key === "plate").label).toBe("Number-Plate (ANPR)");
  expect(e.find((x) => x.key === "face").state).toBe("off");
});

test("measured state comes from the analyzer that runs the service", () => {
  const st = { services: { person: { state: "degraded", fps: 18.5, behind_s: 1.7 }, anpr: { state: "live", fps: 20 } } };
  const e = Object.fromEntries(serviceEntries({ ...MODELS, active: [...MODELS.active, "face"] }, st).map((x) => [x.key, x]));
  expect(e.person).toMatchObject({ state: "degraded", fps: 18.5, behind_s: 1.7 });
  expect(e.face).toMatchObject({ state: "degraded", fps: 18.5 });     // faces run in the person service
  expect(e.plate).toMatchObject({ state: "live", fps: 20 });
  expect(e.crowd).toMatchObject({ state: "starting", fps: null });     // selected, not measured yet
  expect(e.vehicle).toMatchObject({ state: "off", fps: null });
});

test("no models answer yet: everything off", () => {
  expect(serviceEntries(null, undefined).every((x) => !x.enabled && x.state === "off")).toBe(true);
});

test("routes open the service page on the drone", () => {
  const by = Object.fromEntries(ANALYTICS_SERVICES.map((s) => [s.key, s]));
  expect(serviceRoute(by.person, "DRN 01")).toBe("/command/person?drone=DRN+01");
  expect(serviceRoute(by.plate, "D1")).toBe("/command/traffic?drone=D1&view=anpr");
  expect(serviceRoute(by.vessel, "D1")).toBe("/command/traffic?drone=D1&view=vessel");
  expect(serviceRoute(by.anomaly, "D1")).toBe("/command/anomaly?drone=D1");
});
