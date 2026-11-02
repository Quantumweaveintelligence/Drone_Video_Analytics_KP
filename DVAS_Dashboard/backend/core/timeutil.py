"""
Time handling for the dashboard (evidence plan E6, R2).

* UTCDateTime — column type for every dashboard time column: `timestamptz` in
  Postgres, so ordering and range filters compare real instants. It accepts the
  ISO-8601 strings (or datetimes) the code already writes and returns ISO-8601
  UTC strings, so the API output and the frontend stay unchanged.
* IST helpers — reports work in Indian Standard Time calendar days.
"""
import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional, Tuple, Union
from zoneinfo import ZoneInfo

from sqlalchemy.types import DateTime, TypeDecorator

logger = logging.getLogger("dvas.time")
IST = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc


def parse_ts(value: Union[str, datetime, None]) -> Optional[datetime]:
    """ISO-8601 string / datetime → aware UTC datetime. '' / None / unparseable → None.
    Naive values are taken as UTC (how the dashboard has always written them)."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime.combine(value, time.min)
    else:
        try:
            dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def iso_utc(value: Union[str, datetime, None]) -> Optional[str]:
    """Canonical ISO-8601 UTC string, the form UTCDateTime returns from the DB."""
    dt = parse_ts(value)
    return dt.isoformat() if dt else None


class UTCDateTime(TypeDecorator):
    """timestamptz column that speaks ISO-8601 strings to Python code."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None or value == "":
            return None
        dt = parse_ts(value)
        if dt is None:
            # e.g. a time-only "10:15:03" or "--:--:--" placeholder from a client: no instant to store
            logger.warning("Ignoring non-ISO time value %r", str(value)[:40])
            return None
        if dialect.name == "sqlite":          # tests: SQLite stores naive UTC
            return dt.replace(tzinfo=None)
        return dt

    def process_result_value(self, value, dialect):
        return iso_utc(value) if value is not None else None

    def coerce_compared_value(self, op, value):
        # `column >= "2026-11-02"` binds through this type, so strings are parsed
        return self


# ── IST calendar ranges for reports (R2) ─────────────────────────────────────
def ist_day_bounds(day: Union[str, date]) -> Tuple[datetime, datetime]:
    """[start, end) of an IST calendar day, in UTC."""
    d = date.fromisoformat(day) if isinstance(day, str) else day
    start = datetime.combine(d, time.min, tzinfo=IST)
    return start.astimezone(UTC), (start + timedelta(days=1)).astimezone(UTC)


def ist_range(start_day: Union[str, date], end_day: Union[str, date]) -> Tuple[datetime, datetime]:
    """[start of start_day, end of end_day) in IST, as UTC datetimes (end_day inclusive)."""
    s, _ = ist_day_bounds(start_day)
    _, e = ist_day_bounds(end_day)
    if e <= s:
        raise ValueError("end date is before start date")
    return s, e


def today_ist() -> date:
    return datetime.now(IST).date()


def fmt_ist(value, with_zone: bool = True) -> str:
    dt = parse_ts(value)
    if dt is None:
        return "—"
    out = dt.astimezone(IST).strftime("%d-%m-%Y %H:%M")
    return f"{out} IST" if with_zone else out
