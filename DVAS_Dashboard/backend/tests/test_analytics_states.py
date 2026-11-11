"""Live / degraded / shedding per drone for the video tiles (v2 plan R6.4).

Run from DVAS_Dashboard/backend:  python -m pytest tests/test_analytics_states.py -q
"""
from routers.infra import _performance_view, drone_analytics_states

GOV = {"services": {
    "person": {"drones": {"DRN-1": {"fps": 20, "input_fps": 20, "lag_ms": 4200, "shed_level": 2,
                                    "frames_shed": 98, "cadence": 2}}},
    "vehicle": {"drones": {"DRN-1": {"fps": 20, "input_fps": 20, "lag_ms": 1700, "shed_level": 1},
                           "DRN-2": {"fps": 20, "input_fps": 20, "lag_ms": 200}}},
}}


def test_worst_state_per_drone_with_the_reason_per_service():
    s = drone_analytics_states(GOV, ["DRN-1", "DRN-2", "DRN-3"])
    assert s["DRN-1"]["state"] == "shedding"
    assert s["DRN-1"]["services"]["person"] == {"state": "shedding", "behind_s": 4.2, "frames_shed": 98, "cadence": 2}
    assert s["DRN-1"]["services"]["vehicle"]["state"] == "degraded"
    assert s["DRN-2"]["state"] == "live" and s["DRN-3"] == {"state": "live", "services": {}}


def test_only_the_asked_drones_are_returned():
    assert set(drone_analytics_states(GOV, ["DRN-2"])) == {"DRN-2"}


def test_models_page_rows_show_the_shed_state():
    view = _performance_view({**GOV, "enabled": True}, {"target_min": 20}, {"role": "system_administrator"})
    rows = {(r["service"], d["drone_id"]): d for r in view["services"] for d in r["drones"]}
    assert rows[("person", "DRN-1")]["state"] == "shedding" and rows[("person", "DRN-1")]["frames_shed"] == 98
    assert rows[("vehicle", "DRN-1")]["state"] == "degraded"
    assert rows[("vehicle", "DRN-2")]["state"] == "on_target"
