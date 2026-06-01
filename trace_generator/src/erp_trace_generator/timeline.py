"""Synthetic timeline planning."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from random import Random
from zoneinfo import ZoneInfo

from erp_trace_generator.errors import TraceGenerationError
from erp_trace_generator.models import MinuteRange, RunSettings


@dataclass(frozen=True)
class StepWindow:
    start: datetime
    end: datetime


class TimelinePlanner:
    """Plans target synthetic timestamps without sleeping during execution."""

    def __init__(self, settings: RunSettings, rng: Random) -> None:
        self._settings = settings
        self._rng = rng
        self._tz = ZoneInfo(settings.target_timezone)
        self._work_start = _parse_time(settings.working_hours.core_start)
        self._work_end = _parse_time(settings.working_hours.core_end)
        self._pause_start = _parse_time(settings.working_hours.pause_window_start)
        self._day_boundaries: dict[tuple[str, date], _DayBoundaries] = {}

    def first_start(self) -> datetime:
        return datetime.combine(_next_business_day(self._settings.run_start_date), self._work_start, self._tz)

    def add_step_duration(self, start: datetime, step_type: str, delay_multiplier: float, actor_criteria=None) -> datetime:
        return self.plan_step_window(start, step_type, delay_multiplier, actor_criteria).end

    def plan_step_window(self, start: datetime, step_type: str, delay_multiplier: float, actor_criteria=None) -> StepWindow:
        if delay_multiplier <= 0:
            raise TraceGenerationError(f"delay_multiplier must be greater than 0 for step '{step_type}'")
        duration_range = self._settings.step_duration_minutes[step_type]
        sampled_minutes = _sample_int(self._rng, duration_range)
        duration_minutes = max(1, round(sampled_minutes * delay_multiplier))
        return self._fit_into_workday(start, duration_minutes, actor_criteria)

    def add_inter_step_delay(self, end: datetime, from_step_type: str, to_step_type: str) -> datetime:
        delay_range = self._settings.inter_step_delay_minutes.get(
            (from_step_type, to_step_type),
            MinuteRange(min=0, max=0),
        )
        # Future LLM realism compiler can write these ranges into Pkl; v1 samples them deterministically.
        candidate = end + timedelta(minutes=_sample_int(self._rng, delay_range))
        return self.align_start(candidate)

    def align_start(self, candidate: datetime, actor_criteria=None) -> datetime:
        current = candidate
        if current.tzinfo is None:
            current = current.replace(tzinfo=self._tz)
        while True:
            if not _is_business_day(current.date()):
                current = datetime.combine(_next_business_day(current.date()), self._work_start, self._tz)
                continue
            boundaries = self._boundaries_for(current.date(), actor_criteria)
            if current < boundaries.work_start:
                return boundaries.work_start
            if current >= boundaries.work_end:
                current = datetime.combine(current.date() + timedelta(days=1), self._work_start, self._tz)
                continue
            if boundaries.pause_start <= current < boundaries.pause_end:
                current = boundaries.pause_end
                continue
            return current

    def _fit_into_workday(self, start: datetime, duration_minutes: int, actor_criteria=None) -> StepWindow:
        current_start = self.align_start(start, actor_criteria)
        while True:
            end = self._end_on_same_day(current_start, duration_minutes, actor_criteria)
            if end is not None:
                return StepWindow(start=current_start, end=end)
            full_day_start = self.align_start(
                datetime.combine(current_start.date(), self._work_start, self._tz),
                actor_criteria,
            )
            if current_start == full_day_start:
                raise TraceGenerationError(
                    f"step duration {duration_minutes} minutes cannot fit into a single business day"
                )
            current_start = self.align_start(
                datetime.combine(current_start.date() + timedelta(days=1), self._work_start, self._tz),
                actor_criteria,
            )
            continue

    def _end_on_same_day(self, start: datetime, duration_minutes: int, actor_criteria=None) -> datetime | None:
        current = start
        remaining_minutes = duration_minutes
        boundaries = self._boundaries_for(start.date(), actor_criteria)
        while True:
            if current >= boundaries.work_end:
                return None
            segment_end = boundaries.work_end
            if current < boundaries.pause_start:
                segment_end = min(segment_end, boundaries.pause_start)
            elif boundaries.pause_start <= current < boundaries.pause_end:
                current = boundaries.pause_end
                continue

            available_minutes = max(0, int((segment_end - current).total_seconds() // 60))
            if remaining_minutes <= available_minutes:
                return current + timedelta(minutes=remaining_minutes)

            remaining_minutes -= available_minutes
            if segment_end == boundaries.pause_start:
                current = boundaries.pause_end
                continue
            return None

    def _boundaries_for(self, day: date, actor_criteria=None) -> _DayBoundaries:
        key = (_actor_key(actor_criteria), day)
        if key not in self._day_boundaries:
            self._day_boundaries[key] = _DayBoundaries(
                work_start=datetime.combine(day, self._work_start, self._tz),
                work_end=self._work_end_for(day, actor_criteria),
                pause_start=self._pause_start_for(day),
                pause_end=self._pause_end_for(day, actor_criteria),
            )
        return self._day_boundaries[key]

    def _pause_start_for(self, day: date) -> datetime:
        return datetime.combine(day, self._pause_start, self._tz)

    def _pause_end_for(self, day: date, actor_criteria=None) -> datetime:
        pause_minutes = (
            actor_criteria.pause_duration_minutes
            if actor_criteria is not None
            else _sample_int(
                self._rng,
                MinuteRange(
                    min=self._settings.working_hours.pause_duration_minutes_min,
                    max=self._settings.working_hours.pause_duration_minutes_max,
                ),
            )
        )
        return self._pause_start_for(day) + timedelta(minutes=pause_minutes)

    def _work_end_for(self, day: date, actor_criteria=None) -> datetime:
        deviation = (
            actor_criteria.workday_deviation_hours
            if actor_criteria is not None
            else self._rng.uniform(
                self._settings.working_hours.daily_deviation_hours_min,
                self._settings.working_hours.daily_deviation_hours_max,
            )
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


def _is_business_day(day: date) -> bool:
    return day.weekday() < 5


def _next_business_day(day: date) -> date:
    current = day
    while not _is_business_day(current):
        current += timedelta(days=1)
    return current


def _sample_int(rng: Random, value: MinuteRange) -> int:
    return rng.randint(value.min, value.max)


def _actor_key(actor_criteria) -> str:
    return getattr(actor_criteria, "actor_id", "__global__")
