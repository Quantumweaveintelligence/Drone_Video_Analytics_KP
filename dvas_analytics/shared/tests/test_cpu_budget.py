"""Per-service CPU thread budget (v2 plan R4.4).

Run from dvas_analytics/:  python -m pytest shared/tests/test_cpu_budget.py -q
"""
import pytest

from shared import cpu_budget


def test_service_share_wins_over_the_common_default(monkeypatch):
    monkeypatch.setenv("OMP_NUM_THREADS", "3")
    monkeypatch.setenv("DVAS_THREADS_PERSON", "5")
    assert cpu_budget.threads_for("person") == 5
    assert cpu_budget.threads_for("vessel") == 3          # no share of its own


def test_nothing_set_leaves_the_libraries_alone(monkeypatch):
    monkeypatch.delenv("OMP_NUM_THREADS", raising=False)
    monkeypatch.delenv("DVAS_THREADS_CROWD", raising=False)
    assert cpu_budget.threads_for("crowd") is None and cpu_budget.apply("crowd") is None


def test_apply_sets_opencv_and_torch(monkeypatch):
    cv2 = pytest.importorskip("cv2")
    torch = pytest.importorskip("torch")
    monkeypatch.setenv("DVAS_THREADS_ANPR", "2")
    before = (cv2.getNumThreads(), torch.get_num_threads())
    try:
        assert cpu_budget.apply("anpr") == 2
        assert cv2.getNumThreads() == 2 and torch.get_num_threads() == 2
    finally:
        cv2.setNumThreads(before[0])
        torch.set_num_threads(before[1])
