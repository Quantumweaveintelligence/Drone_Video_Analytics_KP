"""core/timeutil.py (evidence plan E6/R2). Run: python -m pytest tests/test_timeutil.py -q"""
from datetime import datetime, timezone

import pytest

from core.timeutil import IST, UTCDateTime, fmt_ist, ist_day_bounds, ist_range, iso_utc, parse_ts


def test_parse_and_canonical_form():
    assert iso_utc("2026-11-02T10:00:00Z") == "2026-11-02T10:00:00+00:00"
    assert iso_utc("2026-11-02T15:30:00+05:30") == "2026-11-02T10:00:00+00:00"
    assert iso_utc("2026-11-02 10:00:00") == "2026-11-02T10:00:00+00:00"      # naive = UTC
    assert parse_ts("") is None and parse_ts("--:--:--") is None and parse_ts(None) is None


def test_ist_day_is_1830_to_1830_utc():
    s, e = ist_day_bounds("2026-11-02")
    assert s == datetime(2026, 11, 1, 18, 30, tzinfo=timezone.utc)
    assert e == datetime(2026, 11, 2, 18, 30, tzinfo=timezone.utc)
    s2, e2 = ist_range("2026-11-02", "2026-11-08")
    assert s2 == s and (e2 - s2).days == 7
    with pytest.raises(ValueError):
        ist_range("2026-11-08", "2026-11-02")


def test_column_type_binds_strings_and_returns_iso():
    t = UTCDateTime()

    class PG:
        name = "postgresql"
    bound = t.process_bind_param("2026-11-02T15:30:00+05:30", PG)
    assert bound == datetime(2026, 11, 2, 10, tzinfo=timezone.utc)
    assert t.process_bind_param("", PG) is None
    assert t.process_bind_param("not a time", PG) is None
    assert t.process_result_value(bound.astimezone(IST), PG) == "2026-11-02T10:00:00+00:00"


def test_fmt_ist():
    assert fmt_ist("2026-11-01T18:30:00Z") == "02-11-2026 00:00 IST"
    assert fmt_ist(None) == "—"
