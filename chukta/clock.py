"""Time.

Two things make time non-trivial here:

  1. Every compliance window is in IST, but events carry UTC timestamps.
  2. The simulator needs to travel through days quickly, so nothing may read
     the wall clock. `Clock` is passed explicitly everywhere for that reason -
     `datetime.now()` must not appear anywhere else in the codebase.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc

# Salary lands in most Indian payrolls at the turn of the month or the first
# week. A funding failure retried on the 28th fails; the same retry on the 1st
# succeeds. These are the dates the funding policy aims at.
SALARY_DAYS = (1, 7)


class Clock:
    """Injectable clock. In the sim it is advanced by hand."""

    def __init__(self, now: datetime):
        if now.tzinfo is None:
            raise ValueError("Clock requires a timezone-aware datetime")
        self._now = now.astimezone(UTC)

    def now(self) -> datetime:
        return self._now

    def advance(self, **kwargs) -> None:
        self._now = self._now + timedelta(**kwargs)

    def set(self, when: datetime) -> None:
        self._now = when.astimezone(UTC)


def to_ist(when: datetime) -> datetime:
    return when.astimezone(IST)


def ist_hour(when: datetime) -> int:
    return to_ist(when).hour


def in_window(when: datetime, window: tuple[int, int]) -> bool:
    """Is `when` inside an IST hour window [start, end)?

    Windows that wrap midnight are supported, so (22, 6) means 22:00-06:00.
    """
    start, end = window
    hour = ist_hour(when)
    if start <= end:
        return start <= hour < end
    return hour >= start or hour < end


def next_time_in_window(when: datetime, window: tuple[int, int]) -> datetime:
    """Earliest instant at or after `when` that falls inside the window."""
    if in_window(when, window):
        return when
    start, _ = window
    local = to_ist(when)
    candidate = local.replace(hour=start, minute=0, second=0, microsecond=0)
    if candidate <= local:
        candidate = candidate + timedelta(days=1)
    return candidate.astimezone(UTC)


def next_salary_date(when: datetime, offset_hours: int = 0) -> datetime:
    """Next payroll-adjacent morning at or after `when`, at 10:00 IST."""
    local = to_ist(when)
    for _ in range(70):  # two months of headroom
        for day in SALARY_DAYS:
            candidate = _same_month_day(local, day).replace(
                hour=10, minute=0, second=0, microsecond=0
            )
            candidate = candidate + timedelta(hours=offset_hours)
            if candidate > local:
                return candidate.astimezone(UTC)
        local = _first_of_next_month(local)
    raise RuntimeError("no salary date found - check SALARY_DAYS")


def next_business_morning(when: datetime) -> datetime:
    """Next 10:00 IST, skipping Sunday."""
    local = to_ist(when)
    candidate = local.replace(hour=10, minute=0, second=0, microsecond=0)
    if candidate <= local:
        candidate = candidate + timedelta(days=1)
    while candidate.weekday() == 6:  # Sunday
        candidate = candidate + timedelta(days=1)
    return candidate.astimezone(UTC)


def _same_month_day(local: datetime, day: int) -> datetime:
    try:
        return local.replace(day=day)
    except ValueError:  # month too short
        return local.replace(day=1)


def _first_of_next_month(local: datetime) -> datetime:
    if local.month == 12:
        return local.replace(year=local.year + 1, month=1, day=1)
    return local.replace(month=local.month + 1, day=1)


def resolve_when(spec: dict, now: datetime, quiet_window: tuple[int, int]) -> datetime:
    """Turn a `when:` block from policy.yaml into a concrete instant."""
    scheduled = now

    if spec.get("next_salary_date"):
        scheduled = next_salary_date(now, int(spec.get("offset_hours", 0)))
    elif spec.get("next_business_morning"):
        scheduled = next_business_morning(now)
    else:
        scheduled = now + timedelta(hours=float(spec.get("delay_hours", 0)))

    if spec.get("respect_quiet_hours"):
        scheduled = next_time_in_window(scheduled, quiet_window)

    return scheduled
