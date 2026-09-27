"""Next-run computation for task schedules, using APScheduler triggers (spec 4.4 / 15.5).

Schedule JSON::

    {"time": "08:00", "weekdays": [0, 3], "interval_hours": 6,
     "start_date": "2026-09-23", "end_date": null,
     "window_start": "06:00", "window_end": "23:00", "min_interval_hours": 6}
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from apscheduler.triggers.base import BaseTrigger
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

SCHEDULE_TYPES = ("manual", "daily", "weekly", "interval", "startup", "reminder")
_DAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def _hm(value: str | None, default: str = "08:00") -> tuple[int, int]:
    raw = value or default
    h, m = raw.split(":")[:2]
    return int(h), int(m)


def _date(value: str | None, tz: ZoneInfo, end: bool = False) -> datetime | None:
    if not value:
        return None
    d = datetime.fromisoformat(value)
    if d.tzinfo is None:
        d = d.replace(tzinfo=tz)
    if end and len(value) <= 10:
        d = d + timedelta(days=1) - timedelta(seconds=1)
    return d


def build_trigger(schedule_type: str, schedule: dict[str, Any], tz_name: str) -> BaseTrigger | None:
    tz = ZoneInfo(tz_name)
    start = _date(schedule.get("start_date"), tz)
    end = _date(schedule.get("end_date"), tz, end=True)
    kind = schedule_type
    if kind == "reminder":
        kind = "weekly" if schedule.get("weekdays") else "daily"
    if kind == "daily":
        h, m = _hm(schedule.get("time"))
        return CronTrigger(hour=h, minute=m, timezone=tz, start_date=start, end_date=end)
    if kind == "weekly":
        days = schedule.get("weekdays") or [0]
        h, m = _hm(schedule.get("time"))
        dow = ",".join(_DAY_NAMES[int(d) % 7] for d in days)
        return CronTrigger(day_of_week=dow, hour=h, minute=m, timezone=tz, start_date=start, end_date=end)
    if kind == "interval":
        hours = float(schedule.get("interval_hours") or 24)
        return IntervalTrigger(hours=hours, timezone=tz, start_date=start or datetime.now(tz), end_date=end)
    return None


def _in_window(dt: datetime, schedule: dict[str, Any], tz: ZoneInfo) -> bool:
    ws, we = schedule.get("window_start"), schedule.get("window_end")
    if not ws or not we:
        return True
    local = dt.astimezone(tz).time()
    start, end = time(*_hm(ws)), time(*_hm(we))
    if start <= end:
        return start <= local <= end
    return local >= start or local <= end  # window spanning midnight


def next_run_after(schedule_type: str, schedule: dict[str, Any], tz_name: str, after: datetime) -> datetime | None:
    trigger = build_trigger(schedule_type, schedule, tz_name)
    if trigger is None:
        return None
    tz = ZoneInfo(tz_name)
    prev: datetime | None = None
    now = after
    for _ in range(2000):
        nxt = trigger.get_next_fire_time(prev, now)
        if nxt is None:
            return None
        if nxt > after and _in_window(nxt, schedule, tz):
            return nxt
        prev = nxt
        now = nxt + timedelta(seconds=1)
    return None
