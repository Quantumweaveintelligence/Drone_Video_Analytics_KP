"""Feed mode per service (v2 plan R6.1): DVAS_FEED_MODE_<SERVICE> overrides the code default.

Run from dvas_analytics:  python -m pytest shared/tests/test_feed_mode.py -q
"""
import pytest

from shared.frame_sampler import FrameSampler


@pytest.mark.parametrize("env,default,expect", [
    (None, "ordered", "ordered"),
    (None, "newest", "newest"),
    ("latest", "ordered", "newest"),
    ("every", "newest", "ordered"),
    ("LATEST", "ordered", "newest"),
    ("bogus", "ordered", "ordered"),           # unknown value: the code default stays
])
def test_env_overrides_the_mode(monkeypatch, env, default, expect):
    if env is None:
        monkeypatch.delenv("DVAS_FEED_MODE_VESSEL", raising=False)
    else:
        monkeypatch.setenv("DVAS_FEED_MODE_VESSEL", env)
    assert FrameSampler("vessel", "vessel-group", "c", mode=default).mode == expect
