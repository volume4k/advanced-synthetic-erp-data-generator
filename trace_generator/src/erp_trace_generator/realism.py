"""Compile natural-language realism inputs into validated scheduling criteria."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
import hashlib
import json
import os
from pathlib import Path
from typing import Protocol
from urllib import request
from urllib.error import URLError
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, ValidationError

from erp_trace_generator.errors import TraceGenerationError
from erp_trace_generator.models import Actor, GenerationConfig


class RealismLLMClient(Protocol):
    def complete_json(self, prompt: str) -> str:
        """Return a JSON object as text."""


class ActorRealismCriteria(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str
    delay_multiplier: float
    workday_deviation_hours: float
    pause_duration_minutes: int
    runtime_delay_cap_seconds: float


class DailyDemandResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: str
    releases: list["DemandReleaseItem"]


class DemandReleaseItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    release_time: str
    material_id: str


@dataclass(frozen=True)
class DemandRelease:
    case_id: str
    release_time: datetime
    material_id: str


@dataclass(frozen=True)
class CompiledRealismCriteria:
    actor_criteria: dict[str, ActorRealismCriteria]
    demand_releases: list[DemandRelease]
    criteria_hash: str
    llm_metadata: dict


class OpenAICompatibleLLMClient:
    """Minimal OpenAI-compatible chat completions client for local hosted models."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        timeout_seconds: int = 60,
    ) -> None:
        self._base_url = (base_url or os.environ.get("REALISM_LLM_BASE_URL") or "").rstrip("/")
        self._model = model or os.environ.get("REALISM_LLM_MODEL")
        self._api_key = api_key if api_key is not None else os.environ.get("REALISM_LLM_API_KEY")
        self._timeout_seconds = timeout_seconds
        if not self._base_url:
            raise TraceGenerationError("REALISM_LLM_BASE_URL is required when realism compilation is enabled")
        if not self._model:
            raise TraceGenerationError("REALISM_LLM_MODEL is required when realism compilation is enabled")

    def complete_json(self, prompt: str) -> str:
        payload = {
            "model": self._model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Return only one strict JSON object. Do not include markdown, comments, or prose."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
        }
        body = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        http_request = request.Request(
            f"{self._base_url}/v1/chat/completions",
            data=body,
            headers=headers,
            method="POST",
        )
        try:
            with request.urlopen(http_request, timeout=self._timeout_seconds) as response:
                response_payload = json.loads(response.read().decode("utf-8"))
        except (OSError, URLError, json.JSONDecodeError) as exc:
            raise TraceGenerationError(f"Realism LLM request failed: {exc}") from exc

        try:
            return str(response_payload["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as exc:
            raise TraceGenerationError("Realism LLM response did not match OpenAI chat completions shape") from exc


class RealismCompiler:
    def __init__(
        self,
        *,
        config: GenerationConfig,
        client: RealismLLMClient,
        cache_dir: str | Path,
        max_retries: int = 3,
    ) -> None:
        if max_retries < 1:
            raise ValueError("max_retries must be >= 1")
        self._config = config
        self._client = client
        self._cache_dir = Path(cache_dir)
        self._max_retries = max_retries

    def actor_cache_path(self, actor_id: str) -> Path:
        return self._cache_dir / f"realism-criteria.actor.{actor_id}.{self._actor_hash(actor_id)}.json"

    def compile(self) -> CompiledRealismCriteria:
        actor_criteria = {actor.id: self.compile_actor(actor.id) for actor in self._config.actors}
        demand_releases = self._compile_demand_releases()
        payload = _criteria_payload(actor_criteria, demand_releases)
        criteria_hash = _criteria_hash(payload)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        (self._cache_dir / f"realism-criteria.{criteria_hash}.json").write_text(
            json.dumps(payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return CompiledRealismCriteria(
            actor_criteria=actor_criteria,
            demand_releases=demand_releases,
            criteria_hash=criteria_hash,
            llm_metadata={"used": True, "realism_criteria_hash": criteria_hash},
        )

    def compile_actor(self, actor_id: str) -> ActorRealismCriteria:
        actor = self._actor(actor_id)
        cache_path = self.actor_cache_path(actor_id)
        if cache_path.exists():
            return self._actor_criteria_from_json(cache_path.read_text(encoding="utf-8"), actor)

        last_error: str | None = None
        for _attempt in range(self._max_retries):
            prompt = self._actor_prompt(actor, last_error)
            raw_response = self._client.complete_json(prompt)
            try:
                criteria = self._actor_criteria_from_json(raw_response, actor)
            except (TraceGenerationError, ValidationError, json.JSONDecodeError) as exc:
                last_error = str(exc)
                continue
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(criteria.model_dump_json(indent=2), encoding="utf-8")
            return criteria

        detail = f": {last_error}" if last_error else ""
        raise TraceGenerationError(f"Could not compile realism criteria for actor '{actor_id}'{detail}")

    def compile_daily_demand(
        self,
        day: str,
        remaining_count: int,
        *,
        case_start_index: int = 1,
    ) -> list[DemandRelease]:
        last_error: str | None = None
        for _attempt in range(self._max_retries):
            prompt = self._daily_demand_prompt(day, remaining_count, last_error)
            raw_response = self._client.complete_json(prompt)
            try:
                return self._demand_releases_from_json(raw_response, day, remaining_count, case_start_index)
            except (TraceGenerationError, ValidationError, json.JSONDecodeError, ValueError) as exc:
                last_error = str(exc)
                continue

        detail = f": {last_error}" if last_error else ""
        raise TraceGenerationError(f"Could not compile demand releases for {day}{detail}")

    def _compile_demand_releases(self) -> list[DemandRelease]:
        releases: list[DemandRelease] = []
        remaining = self._config.run_settings.case_count
        current_day = self._config.run_settings.run_start_date
        for _day_index in range(self._config.run_settings.run_horizon_days):
            daily = self.compile_daily_demand(
                current_day.isoformat(),
                remaining,
                case_start_index=len(releases) + 1,
            )
            releases.extend(daily)
            remaining -= len(daily)
            if remaining == 0:
                return releases
            current_day += timedelta(days=1)
        raise TraceGenerationError(
            f"Compiled demand releases produced {len(releases)} process cases, "
            f"but caseCount requires {self._config.run_settings.case_count}"
        )

    def _actor_criteria_from_json(self, raw_response: str, actor: Actor) -> ActorRealismCriteria:
        payload = json.loads(raw_response)
        criteria = ActorRealismCriteria.model_validate(payload)
        self._validate_actor_criteria(actor, criteria)
        return criteria

    def _demand_releases_from_json(
        self,
        raw_response: str,
        expected_day: str,
        remaining_count: int,
        case_start_index: int,
    ) -> list[DemandRelease]:
        payload = json.loads(raw_response)
        response = DailyDemandResponse.model_validate(payload)
        if response.date != expected_day:
            raise TraceGenerationError(f"demand response date '{response.date}' did not match requested date '{expected_day}'")
        if len(response.releases) > remaining_count:
            raise TraceGenerationError(
                f"demand response returned {len(response.releases)} releases with only {remaining_count} remaining"
            )
        master_data_by_material_id = {entry.material_id: entry for entry in self._config.master_data}
        known_material_ids = set(master_data_by_material_id)
        target_day = date.fromisoformat(expected_day)
        tz = ZoneInfo(self._config.run_settings.target_timezone)
        run_end_exclusive = self._config.run_settings.run_start_date + timedelta(days=self._config.run_settings.run_horizon_days)
        if not self._config.run_settings.run_start_date <= target_day < run_end_exclusive:
            raise TraceGenerationError(f"demand date '{expected_day}' is outside run horizon")

        releases: list[DemandRelease] = []
        for index, release in enumerate(response.releases, start=case_start_index):
            if release.material_id not in known_material_ids:
                raise TraceGenerationError(f"unknown material_id '{release.material_id}' in demand release")
            release_clock = time.fromisoformat(release.release_time)
            release_at = datetime.combine(target_day, release_clock, tz)
            self._validate_release_inside_workday(release_at)
            self._validate_release_can_finish_inside_horizon(
                release_at,
                master_data_by_material_id[release.material_id].delivery_lead_time_min_days,
            )
            releases.append(DemandRelease(case_id=f"C{index:03d}", release_time=release_at, material_id=release.material_id))
        return releases

    def _validate_actor_criteria(self, actor: Actor, criteria: ActorRealismCriteria) -> None:
        if criteria.actor_id != actor.id:
            raise TraceGenerationError(f"actor_id '{criteria.actor_id}' did not match requested actor '{actor.id}'")
        guardrails = actor.realism_guardrails
        if not guardrails.delay_multiplier_min <= criteria.delay_multiplier <= guardrails.delay_multiplier_max:
            raise TraceGenerationError(
                f"delay_multiplier {criteria.delay_multiplier} outside guardrails "
                f"[{guardrails.delay_multiplier_min}, {guardrails.delay_multiplier_max}]"
            )
        if not guardrails.workday_deviation_hours_min <= criteria.workday_deviation_hours <= guardrails.workday_deviation_hours_max:
            raise TraceGenerationError(
                f"workday_deviation_hours {criteria.workday_deviation_hours} outside guardrails "
                f"[{guardrails.workday_deviation_hours_min}, {guardrails.workday_deviation_hours_max}]"
            )
        if not guardrails.pause_duration_minutes_min <= criteria.pause_duration_minutes <= guardrails.pause_duration_minutes_max:
            raise TraceGenerationError(
                f"pause_duration_minutes {criteria.pause_duration_minutes} outside guardrails "
                f"[{guardrails.pause_duration_minutes_min}, {guardrails.pause_duration_minutes_max}]"
            )
        if not guardrails.runtime_delay_cap_seconds_min <= criteria.runtime_delay_cap_seconds <= guardrails.runtime_delay_cap_seconds_max:
            raise TraceGenerationError(
                f"runtime_delay_cap_seconds {criteria.runtime_delay_cap_seconds} outside guardrails "
                f"[{guardrails.runtime_delay_cap_seconds_min}, {guardrails.runtime_delay_cap_seconds_max}]"
            )

    def _validate_release_inside_workday(self, release_at: datetime) -> None:
        work_start = time.fromisoformat(self._config.run_settings.working_hours.core_start)
        work_end = time.fromisoformat(self._config.run_settings.working_hours.core_end)
        if not work_start <= release_at.timetz().replace(tzinfo=None) < work_end:
            raise TraceGenerationError(f"demand release '{release_at.isoformat()}' is outside configured working hours")

    def _validate_release_can_finish_inside_horizon(self, release_at: datetime, delivery_lead_time_min_days: int) -> None:
        run_end_exclusive = self._config.run_settings.run_start_date + timedelta(days=self._config.run_settings.run_horizon_days)
        earliest_payment_date = release_at.date() + timedelta(days=delivery_lead_time_min_days + 1)
        if earliest_payment_date >= run_end_exclusive:
            raise TraceGenerationError(
                f"demand release '{release_at.isoformat()}' cannot finish inside run horizon; "
                f"earliest payment date is {earliest_payment_date.isoformat()}"
            )

    def _actor_prompt(self, actor: Actor, last_error: str | None) -> str:
        guardrails = actor.realism_guardrails
        prompt = {
            "task": "Compile actor realism criteria for one synthetic ERP actor.",
            "actor": {
                "actor_id": actor.id,
                "role": actor.role,
                "persona_description": actor.persona_description,
            },
            "guardrails": {
                "delay_multiplier": [guardrails.delay_multiplier_min, guardrails.delay_multiplier_max],
                "workday_deviation_hours": [
                    guardrails.workday_deviation_hours_min,
                    guardrails.workday_deviation_hours_max,
                ],
                "pause_duration_minutes": [
                    guardrails.pause_duration_minutes_min,
                    guardrails.pause_duration_minutes_max,
                ],
                "runtime_delay_cap_seconds": [
                    guardrails.runtime_delay_cap_seconds_min,
                    guardrails.runtime_delay_cap_seconds_max,
                ],
            },
            "required_json_shape": {
                "actor_id": actor.id,
                "delay_multiplier": "number",
                "workday_deviation_hours": "number",
                "pause_duration_minutes": "integer",
                "runtime_delay_cap_seconds": "number",
            },
        }
        if last_error:
            prompt["previous_error"] = f"Validation failed: {last_error}"
        return json.dumps(prompt, sort_keys=True)

    def _daily_demand_prompt(self, day: str, remaining_count: int, last_error: str | None) -> str:
        prompt = {
            "task": "Compile demand releases for one day. Output only Process Case releases, not process steps.",
            "date": day,
            "remaining_case_count": remaining_count,
            "working_hours": {
                "core_start": self._config.run_settings.working_hours.core_start,
                "core_end": self._config.run_settings.working_hours.core_end,
            },
            "allowed_material_ids": [entry.material_id for entry in self._config.master_data],
            "required_json_shape": {
                "date": day,
                "releases": [
                    {
                        "release_time": "HH:MM",
                        "material_id": "one allowed material id",
                    }
                ],
            },
        }
        if last_error:
            prompt["previous_error"] = f"Validation failed: {last_error}"
        return json.dumps(prompt, sort_keys=True)

    def _actor(self, actor_id: str) -> Actor:
        actor = next((item for item in self._config.actors if item.id == actor_id), None)
        if actor is None:
            raise TraceGenerationError(f"unknown actor_id '{actor_id}'")
        return actor

    def _actor_hash(self, actor_id: str) -> str:
        actor_payload = next((item for item in self._config.raw.get("actors", []) if item.get("id") == actor_id), None)
        encoded = json.dumps(actor_payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()[:16]


def default_realism_criteria(config: GenerationConfig) -> CompiledRealismCriteria:
    actor_criteria = {
        actor.id: ActorRealismCriteria(
            actor_id=actor.id,
            delay_multiplier=actor.delay_multiplier,
            workday_deviation_hours=0.0,
            pause_duration_minutes=config.run_settings.working_hours.pause_duration_minutes_min,
            runtime_delay_cap_seconds=actor.runtime_delay_cap_seconds,
        )
        for actor in config.actors
    }
    demand_releases = default_demand_releases(config)
    payload = _criteria_payload(actor_criteria, demand_releases)
    criteria_hash = _criteria_hash(payload)
    return CompiledRealismCriteria(
        actor_criteria=actor_criteria,
        demand_releases=demand_releases,
        criteria_hash=criteria_hash,
        llm_metadata={"used": False, "realism_criteria_hash": criteria_hash},
    )


def compile_realism_criteria(
    *,
    config: GenerationConfig,
    client: RealismLLMClient | None = None,
    cache_dir: str | Path | None = None,
) -> CompiledRealismCriteria:
    if not config.run_settings.realism.enabled:
        return default_realism_criteria(config)
    compiler = RealismCompiler(
        config=config,
        client=client or OpenAICompatibleLLMClient(),
        cache_dir=cache_dir or config.run_settings.realism.cache_dir,
        max_retries=config.run_settings.realism.max_retries,
    )
    return compiler.compile()


def default_demand_releases(config: GenerationConfig) -> list[DemandRelease]:
    tz = ZoneInfo(config.run_settings.target_timezone)
    start = datetime.combine(
        config.run_settings.run_start_date,
        time.fromisoformat(config.run_settings.working_hours.core_start),
        tz,
    )
    return [
        DemandRelease(
            case_id=f"C{index:03d}",
            release_time=start + timedelta(minutes=30 * (index - 1)),
            material_id=config.master_data[(index - 1) % len(config.master_data)].material_id,
        )
        for index in range(1, config.run_settings.case_count + 1)
    ]


def _criteria_payload(
    actor_criteria: dict[str, ActorRealismCriteria],
    demand_releases: list[DemandRelease],
) -> dict:
    return {
        "actor_criteria": {
            actor_id: criteria.model_dump(mode="json")
            for actor_id, criteria in sorted(actor_criteria.items())
        },
        "demand_releases": [
            {
                "case_id": release.case_id,
                "release_time": release.release_time.isoformat(),
                "material_id": release.material_id,
            }
            for release in demand_releases
        ],
    }


def _criteria_hash(payload: dict) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
