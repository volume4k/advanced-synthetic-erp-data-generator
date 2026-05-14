"""Synthetic timeline planning."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from random import Random
from zoneinfo import ZoneInfo

from erp_trace_generator.errors import TraceGenerationError
from erp_trace_generator.models import MinuteRange, RunSettings


class TimelinePlanner:
    """Plans target synthetic timestamps without sleeping during execution."""

    def __init__(self, settings: RunSettings, rng: Random) -> None:
        self._settings = settings
        self._rng = rng
        self._tz = ZoneInfo(settings.target_timezone)
        self._work_start = _parse_time(settings.working_hours.core_start)
        self._work_end = _parse_time(settings.working_hours.core_end)
        self._pause_start = _parse_time(settings.working_hours.pause_window_start)
        self._day_boundaries: dict[date, _DayBoundaries] = {}

    def first_start(self) -> datetime:
        return datetime.combine(self._settings.run_start_date, self._work_start, self._tz)

    def add_step_duration(self, start: datetime, step_type: str, speed_factor: float) -> datetime:
        if speed_factor <= 0:
            raise TraceGenerationError(f"speed_factor must be greater than 0 for step '{step_type}'")
        duration_range = self._settings.step_duration_minutes[step_type]
        sampled_minutes = _sample_int(self._rng, duration_range)
        duration_minutes = max(1, round(sampled_minutes / speed_factor))
        return self._fit_into_workday(start, duration_minutes)

    def add_inter_step_delay(self, end: datetime, from_step_type: str, to_step_type: str) -> datetime:
        delay_range = self._settings.inter_step_delay_minutes.get(
            (from_step_type, to_step_type),
            MinuteRange(min=0, max=0),
        )
        # Future LLM realism compiler can write these ranges into Pkl; v1 samples them deterministically.
        candidate = end + timedelta(minutes=_sample_int(self._rng, delay_range))
        return self.align_start(candidate)

    def align_start(self, candidate: datetime) -> datetime:
        current = candidate
        if current.tzinfo is None:
            current = current.replace(tzinfo=self._tz)
        while True:
            boundaries = self._boundaries_for(current.date())
            if current < boundaries.work_start:
                return boundaries.work_start
            if current >= boundaries.work_end:
                current = datetime.combine(current.date() + timedelta(days=1), self._work_start, self._tz)
                continue
            if boundaries.pause_start <= current < boundaries.pause_end:
                current = boundaries.pause_end
                continue
            return current

    def _fit_into_workday(self, start: datetime, duration_minutes: int) -> datetime:
        current_start = self.align_start(start)
        remaining_minutes = duration_minutes
        while True:
            boundaries = self._boundaries_for(current_start.date())
            segment_end = boundaries.work_end
            if current_start < boundaries.pause_start:
                segment_end = min(segment_end, boundaries.pause_start)

            available_minutes = max(0, int((segment_end - current_start).total_seconds() // 60))
            if remaining_minutes <= available_minutes:
                return current_start + timedelta(minutes=remaining_minutes)

            remaining_minutes -= available_minutes
            if segment_end == boundaries.pause_start:
                current_start = boundaries.pause_end
                continue
            current_start = datetime.combine(current_start.date() + timedelta(days=1), self._work_start, self._tz)

    def _boundaries_for(self, day: date) -> _DayBoundaries:
        if day not in self._day_boundaries:
            self._day_boundaries[day] = _DayBoundaries(
                work_start=datetime.combine(day, self._work_start, self._tz),
                work_end=self._work_end_for(day),
                pause_start=self._pause_start_for(day),
                pause_end=self._pause_end_for(day),
            )
        return self._day_boundaries[day]

    def _pause_start_for(self, day: date) -> datetime:
        return datetime.combine(day, self._pause_start, self._tz)

    def _pause_end_for(self, day: date) -> datetime:
        pause_minutes = _sample_int(
            self._rng,
            MinuteRange(
                min=self._settings.working_hours.pause_duration_minutes_min,
                max=self._settings.working_hours.pause_duration_minutes_max,
            ),
        )
        return self._pause_start_for(day) + timedelta(minutes=pause_minutes)

    def _work_end_for(self, day: date) -> datetime:
        deviation = self._rng.uniform(
            self._settings.working_hours.daily_deviation_hours_min,
            self._settings.working_hours.daily_deviation_hours_max,
        )
        return datetime.combine(day, self._work_end, self._tz) + timedelta(hours=deviation)


@dataclass(frozen=True)
class _DayBoundaries:
    work_start: datetime
    work_end: datetime
    pause_start: datetime
    pause_end: datetime


def _parse_time(value: str) -> time:
    return time.fromisoformat(value)


def _sample_int(rng: Random, value: MinuteRange) -> int:
    return rng.randint(value.min, value.max)
