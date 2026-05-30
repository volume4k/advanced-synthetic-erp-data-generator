"""Compile natural-language realism inputs into validated scheduling criteria."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
import hashlib
import json
import os
from pathlib import Path
from random import Random
from typing import Literal, Protocol
from urllib import request
from urllib.error import URLError
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, ValidationError

from erp_trace_generator.errors import TraceGenerationError
from erp_trace_generator.models import Actor, GenerationConfig, MasterDataEntry


REALISM_COMPILER_SCHEMA_VERSION = "4"
WORKLOAD_FACTORS = {"low": -0.5, "normal": 0.0, "high": 1.0}


class RealismLLMClient(Protocol):
    def complete_json(self, prompt: str) -> str:
        """Return a JSON object as text."""


class ActorRealismCriteria(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actor_id: str
    delay_multiplier: float
    workday_deviation_hours: float
    pause_duration_minutes: int
    day_delay_multiplier_variance: float = 0.0
    day_workday_deviation_hours_variance: float = 0.0
    day_pause_duration_minutes_variance: int = 0
    workload_delay_multiplier_boost: float = 0.0
    workload_workday_deviation_hours_boost: float = 0.0


class DailyDemandResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: str
    releases: list["DemandReleaseItem"]


class DemandReleaseItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    release_time: str
    material_id: str


class PriceAnchorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    material_prices: list["PriceAnchor"]


class PriceAnchor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    material_id: str
    anchor_price: float
    typical_variation_pct: float
    daily_trend_pct: float


class MaterialDemandProfileResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    material_profiles: list["MaterialDemandProfile"]


class MaterialDemandProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    material_id: str
    relative_demand_weight: int
    typical_order_quantity: int
    quantity_variation_pct: float
    bulk_order_share: float
    order_multiple: int


class HorizonDemandResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    patterns: list["DemandPattern"]


class DemandPattern(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: str
    case_count: int
    workload_intensity: Literal["low", "normal", "high"]
    release_windows: list["ReleaseWindow"]
    lead_time_mix: list["LeadTimeMixItem"]


class ReleaseWindow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: str
    end: str
    share: float


class LeadTimeMixItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    days: int
    share: float


@dataclass(frozen=True)
class DemandRelease:
    case_id: str
    release_time: datetime
    material_id: str
    requested_delivery_date: date | None = None
    target_price: float | None = None
    target_quantity: int | None = None
    workload_intensity: str = "normal"


@dataclass(frozen=True)
class CompiledRealismCriteria:
    actor_criteria: dict[str, ActorRealismCriteria]
    demand_releases: list[DemandRelease]
    criteria_hash: str
    llm_metadata: dict
    actor_day_profiles: dict[tuple[str, str], ActorRealismCriteria]
    price_anchors: dict[str, PriceAnchor]
    material_demand_profiles: dict[str, MaterialDemandProfile]
    demand_patterns: list[DemandPattern]


class OpenAICompatibleLLMClient:
    """Minimal OpenAI-compatible chat completions client for local hosted models."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        timeout_seconds: int | None = None,
    ) -> None:
        self._base_url = (base_url or os.environ.get("REALISM_LLM_BASE_URL") or "").rstrip("/")
        self._model = model or os.environ.get("REALISM_LLM_MODEL")
        self._api_key = api_key if api_key is not None else os.environ.get("REALISM_LLM_API_KEY")
        self._timeout_seconds = timeout_seconds if timeout_seconds is not None else _llm_timeout_seconds_from_env()
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
                        "You are a JSON generator. Return only one raw JSON object. "
                        "Do not include markdown, code fences, comments, wrappers, or prose."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.0,
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
        self._llm_request_count = 0
        self._llm_retry_count = 0
        self._cache_hit_count = 0

    def _active_master_data(self) -> tuple[MasterDataEntry, ...]:
        return _active_master_data(self._config)

    def actor_cache_path(self, actor_id: str) -> Path:
        return self._cache_dir / f"realism-criteria.actor.{actor_id}.{self._actor_hash(actor_id)}.json"

    def compile(self) -> CompiledRealismCriteria:
        actor_criteria = {actor.id: self.compile_actor(actor.id) for actor in self._config.actors}
        price_anchors = self.compile_price_anchors()
        material_demand_profiles = self.compile_material_demand_profiles()
        demand_patterns = self.compile_horizon_demand_patterns()
        demand_releases = self.expand_demand_patterns(demand_patterns, price_anchors, material_demand_profiles)
        actor_day_profiles = self._compile_actor_day_profiles(actor_criteria, demand_patterns)
        payload = _criteria_payload(
            actor_criteria,
            demand_releases,
            actor_day_profiles,
            price_anchors,
            material_demand_profiles,
            demand_patterns,
        )
        criteria_hash = _criteria_hash(payload)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        (self._cache_dir / f"realism-criteria.{criteria_hash}.json").write_text(
            json.dumps(payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return CompiledRealismCriteria(
            actor_criteria=actor_criteria,
            actor_day_profiles=actor_day_profiles,
            demand_releases=demand_releases,
            demand_patterns=demand_patterns,
            price_anchors=price_anchors,
            material_demand_profiles=material_demand_profiles,
            criteria_hash=criteria_hash,
            llm_metadata={
                "used": True,
                "realism_criteria_hash": criteria_hash,
                "realism_compiler_schema_version": REALISM_COMPILER_SCHEMA_VERSION,
                "llm_request_count": self._llm_request_count,
                "llm_retry_count": self._llm_retry_count,
                "cache_hit_count": self._cache_hit_count,
            },
        )

    def compile_actor(self, actor_id: str) -> ActorRealismCriteria:
        actor = self._actor(actor_id)
        cache_path = self.actor_cache_path(actor_id)
        if cache_path.exists():
            self._cache_hit_count += 1
            return self._actor_criteria_from_json(cache_path.read_text(encoding="utf-8"), actor)

        last_error: str | None = None
        for attempt in range(self._max_retries):
            try:
                prompt = self._actor_prompt(actor, last_error)
                raw_response = self._complete_json(prompt)
                criteria = self._actor_criteria_from_json(raw_response, actor)
            except (TraceGenerationError, ValidationError, json.JSONDecodeError) as exc:
                last_error = str(exc)
                if attempt < self._max_retries - 1:
                    self._llm_retry_count += 1
                continue
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(criteria.model_dump_json(indent=2), encoding="utf-8")
            return criteria

        detail = f": {last_error}" if last_error else ""
        raise TraceGenerationError(f"Could not compile realism criteria for actor '{actor_id}'{detail}")

    def compile_price_anchors(self) -> dict[str, PriceAnchor]:
        last_error: str | None = None
        for attempt in range(self._max_retries):
            try:
                raw_response = self._complete_json(self._price_anchor_prompt(last_error))
                anchors = self._price_anchors_from_json(raw_response)
            except (TraceGenerationError, ValidationError, json.JSONDecodeError) as exc:
                last_error = str(exc)
                if attempt < self._max_retries - 1:
                    self._llm_retry_count += 1
                continue
            return anchors
        detail = f": {last_error}" if last_error else ""
        raise TraceGenerationError(f"Could not compile price anchors{detail}")

    def compile_material_demand_profiles(self) -> dict[str, MaterialDemandProfile]:
        last_error: str | None = None
        for attempt in range(self._max_retries):
            try:
                raw_response = self._complete_json(self._material_demand_profile_prompt(last_error))
                return self._material_demand_profiles_from_json(raw_response)
            except (TraceGenerationError, ValidationError, json.JSONDecodeError) as exc:
                last_error = str(exc)
                if attempt < self._max_retries - 1:
                    self._llm_retry_count += 1
                continue
        detail = f": {last_error}" if last_error else ""
        raise TraceGenerationError(f"Could not compile material demand profiles{detail}")

    def compile_horizon_demand_patterns(self) -> list[DemandPattern]:
        last_error: str | None = None
        for attempt in range(self._max_retries):
            try:
                raw_response = self._complete_json(self._horizon_demand_prompt(last_error))
                return self._demand_patterns_from_json(raw_response)
            except (TraceGenerationError, ValidationError, json.JSONDecodeError, ValueError) as exc:
                last_error = str(exc)
                if attempt < self._max_retries - 1:
                    self._llm_retry_count += 1
                continue
        detail = f": {last_error}" if last_error else ""
        raise TraceGenerationError(f"Could not compile horizon demand patterns{detail}")

    def expand_demand_patterns(
        self,
        demand_patterns: list[DemandPattern],
        price_anchors: dict[str, PriceAnchor],
        material_demand_profiles: dict[str, MaterialDemandProfile],
    ) -> list[DemandRelease]:
        tz = ZoneInfo(self._config.run_settings.target_timezone)
        slots: list[tuple[datetime, int, str]] = []
        active_master_data = self._active_master_data()
        master_data_by_material_id = {entry.material_id: entry for entry in active_master_data}

        for pattern in sorted(demand_patterns, key=lambda item: item.date):
            pattern_date = date.fromisoformat(pattern.date)
            window_counts = _allocate_counts(pattern.case_count, [item.share for item in pattern.release_windows])
            lead_days = _expand_weighted_values(
                pattern.case_count,
                [(item.days, item.share) for item in pattern.lead_time_mix],
            )
            release_times: list[datetime] = []
            for window, count in zip(pattern.release_windows, window_counts, strict=True):
                release_times.extend(_release_times_for_window(pattern_date, window, count, tz, self._config.run_settings.scheduler_seed))
            release_times.sort()
            for item_index, release_at in enumerate(release_times):
                slots.append((release_at, lead_days[item_index], pattern.workload_intensity))

        slots.sort(key=lambda item: item[0])
        profiles_for_allocation = [
            material_demand_profiles[item.material_id]
            for item in active_master_data
            if item.material_id in material_demand_profiles
        ]
        material_ids = _material_ids_for_releases(
            len(slots),
            profiles_for_allocation,
            seed=self._config.run_settings.scheduler_seed,
            max_share=self._config.run_settings.realism.max_material_share_per_horizon,
        )
        expanded: list[DemandRelease] = []
        for item_index, ((release_at, days, workload_intensity), material_id) in enumerate(zip(slots, material_ids, strict=True)):
            master_data = master_data_by_material_id[material_id]
            profile = material_demand_profiles[material_id]
            requested_delivery_date = release_at.date() + timedelta(days=days)
            self._validate_requested_delivery_date(release_at, requested_delivery_date, master_data)
            target_price = self._sample_target_price(price_anchors[material_id], master_data, release_at, item_index)
            target_quantity = self._sample_target_quantity(profile, master_data, release_at, item_index)
            expanded.append(
                DemandRelease(
                    case_id="",
                    release_time=release_at,
                    material_id=material_id,
                    requested_delivery_date=requested_delivery_date,
                    target_price=target_price,
                    target_quantity=target_quantity,
                    workload_intensity=workload_intensity,
                )
            )

        expanded.sort(key=lambda item: item.release_time)
        return [
            DemandRelease(
                case_id=f"C{index:03d}",
                release_time=release.release_time,
                material_id=release.material_id,
                requested_delivery_date=release.requested_delivery_date,
                target_price=release.target_price,
                target_quantity=release.target_quantity,
                workload_intensity=release.workload_intensity,
            )
            for index, release in enumerate(expanded, start=1)
        ]

    def compile_daily_demand(
        self,
        day: str,
        remaining_count: int,
        *,
        case_start_index: int = 1,
    ) -> list[DemandRelease]:
        last_error: str | None = None
        for attempt in range(self._max_retries):
            try:
                prompt = self._daily_demand_prompt(day, remaining_count, last_error)
                raw_response = self._complete_json(prompt)
                return self._demand_releases_from_json(raw_response, day, remaining_count, case_start_index)
            except (TraceGenerationError, ValidationError, json.JSONDecodeError, ValueError) as exc:
                last_error = str(exc)
                if attempt < self._max_retries - 1:
                    self._llm_retry_count += 1
                continue

        detail = f": {last_error}" if last_error else ""
        raise TraceGenerationError(f"Could not compile demand releases for {day}{detail}")

    def _complete_json(self, prompt: str) -> str:
        self._llm_request_count += 1
        return self._client.complete_json(prompt)

    def _actor_criteria_from_json(self, raw_response: str, actor: Actor) -> ActorRealismCriteria:
        payload = _normalize_actor_payload(_load_json_object(raw_response), actor)
        criteria = ActorRealismCriteria.model_validate(payload)
        self._validate_actor_criteria(actor, criteria)
        return criteria

    def _price_anchors_from_json(self, raw_response: str) -> dict[str, PriceAnchor]:
        payload = _load_json_object(raw_response)
        response = PriceAnchorResponse.model_validate(payload)
        returned_material_ids = [item.material_id for item in response.material_prices]
        duplicates = sorted(
            material_id
            for material_id in set(returned_material_ids)
            if returned_material_ids.count(material_id) > 1
        )
        if duplicates:
            raise TraceGenerationError(
                "price anchor response must include each configured material id exactly once; "
                f"duplicates={duplicates}"
            )
        anchors = {item.material_id: item for item in response.material_prices}
        active_master_data = self._active_master_data()
        expected_material_ids = [entry.material_id for entry in active_master_data]
        expected_material_id_set = set(expected_material_ids)
        returned_material_id_set = set(anchors)
        if returned_material_id_set != expected_material_id_set:
            missing = [material_id for material_id in expected_material_ids if material_id not in returned_material_id_set]
            unexpected = sorted(returned_material_id_set - expected_material_id_set)
            raise TraceGenerationError(
                "price anchor response must include exactly the configured material ids; "
                f"missing={missing}; unexpected={unexpected}; required={expected_material_ids}"
            )
        for material in active_master_data:
            anchor = anchors[material.material_id]
            if not material.price_min <= anchor.anchor_price <= material.price_max:
                raise TraceGenerationError(f"anchor_price for material '{material.material_id}' outside price guardrails")
            if not 0 <= anchor.typical_variation_pct <= self._config.run_settings.realism.max_price_variation_pct:
                raise TraceGenerationError(f"typical_variation_pct for material '{material.material_id}' outside guardrails")
            if abs(anchor.daily_trend_pct) > self._config.run_settings.realism.max_daily_price_trend_pct:
                raise TraceGenerationError(f"daily_trend_pct for material '{material.material_id}' outside guardrails")
        return anchors

    def _material_demand_profiles_from_json(self, raw_response: str) -> dict[str, MaterialDemandProfile]:
        payload = _load_json_object(raw_response)
        response = MaterialDemandProfileResponse.model_validate(payload)
        active_master_data = self._active_master_data()
        expected_material_ids = [entry.material_id for entry in active_master_data]
        returned_material_ids = [item.material_id for item in response.material_profiles]
        expected_material_id_set = set(expected_material_ids)
        returned_material_id_set = set(returned_material_ids)
        duplicates = sorted(
            material_id
            for material_id in returned_material_id_set
            if returned_material_ids.count(material_id) > 1
        )
        unexpected = sorted(returned_material_id_set - expected_material_id_set)
        missing = [material_id for material_id in expected_material_ids if material_id not in returned_material_id_set]
        if duplicates or unexpected or (
            self._config.run_settings.realism.require_all_active_materials_in_demand_profile
            and missing
        ):
            raise TraceGenerationError(
                "material demand profile response must include exactly the configured material ids; "
                f"missing={missing}; unexpected={unexpected}; duplicates={duplicates}; required={expected_material_ids}"
            )
        profiles = {item.material_id: item for item in response.material_profiles}
        if not profiles:
            raise TraceGenerationError("material demand profile response must include at least one configured material id")
        master_data_by_material_id = {entry.material_id: entry for entry in active_master_data}
        settings = self._config.run_settings.realism
        for material_id in returned_material_ids:
            profile = profiles[material_id]
            material = master_data_by_material_id[material_id]
            if not settings.relative_demand_weight_min <= profile.relative_demand_weight <= settings.relative_demand_weight_max:
                raise TraceGenerationError(f"relative_demand_weight for material '{material_id}' outside guardrails")
            if not material.quantity_min <= profile.typical_order_quantity <= material.quantity_max:
                raise TraceGenerationError(f"typical_order_quantity for material '{material_id}' outside quantity guardrails")
            if not settings.quantity_variation_pct_min <= profile.quantity_variation_pct <= settings.quantity_variation_pct_max:
                raise TraceGenerationError(f"quantity_variation_pct for material '{material_id}' outside guardrails")
            if not 0 <= profile.bulk_order_share <= settings.max_bulk_order_share:
                raise TraceGenerationError(f"bulk_order_share for material '{material_id}' outside guardrails")
            profiles[material_id] = profile.model_copy(update={"order_multiple": material.order_multiple})
        return profiles

    def _demand_patterns_from_json(self, raw_response: str) -> list[DemandPattern]:
        payload = _load_json_object(raw_response)
        if "patterns" in payload:
            payload = {"patterns": payload["patterns"]}
        response = HorizonDemandResponse.model_validate(payload)
        seen_dates: set[str] = set()
        total = sum(pattern.case_count for pattern in response.patterns)
        if total != self._config.run_settings.case_count:
            raise TraceGenerationError(
                f"demand patterns total {total} cases, but caseCount requires {self._config.run_settings.case_count}"
            )
        run_end_exclusive = self._config.run_settings.run_start_date + timedelta(days=self._config.run_settings.run_horizon_days)
        for pattern in response.patterns:
            if pattern.date in seen_dates:
                raise TraceGenerationError(f"duplicate demand pattern date '{pattern.date}'")
            seen_dates.add(pattern.date)
            pattern_date = date.fromisoformat(pattern.date)
            if not self._config.run_settings.run_start_date <= pattern_date < run_end_exclusive:
                raise TraceGenerationError(f"demand pattern date '{pattern.date}' is outside run horizon")
            if not self._config.run_settings.realism.daily_case_count_min <= pattern.case_count <= self._config.run_settings.realism.daily_case_count_max:
                raise TraceGenerationError(f"demand pattern case_count {pattern.case_count} outside guardrails")
            _validate_share_sum([item.share for item in pattern.release_windows], "release_windows")
            _validate_share_sum([item.share for item in pattern.lead_time_mix], "lead_time_mix")
            for window in pattern.release_windows:
                start = time.fromisoformat(window.start)
                end = time.fromisoformat(window.end)
                if start >= end:
                    raise TraceGenerationError("release window start must be before end")
                self._validate_release_inside_workday(datetime.combine(pattern_date, start, ZoneInfo(self._config.run_settings.target_timezone)))
                self._validate_release_inside_workday(datetime.combine(pattern_date, (datetime.combine(pattern_date, end) - timedelta(minutes=1)).time(), ZoneInfo(self._config.run_settings.target_timezone)))
            self._validate_pattern_lead_times(pattern)
        return response.patterns

    def _demand_releases_from_json(
        self,
        raw_response: str,
        expected_day: str,
        remaining_count: int,
        case_start_index: int,
    ) -> list[DemandRelease]:
        payload = _load_json_object(raw_response)
        response = DailyDemandResponse.model_validate(payload)
        if response.date != expected_day:
            raise TraceGenerationError(f"demand response date '{response.date}' did not match requested date '{expected_day}'")
        if len(response.releases) > remaining_count:
            raise TraceGenerationError(
                f"demand response returned {len(response.releases)} releases with only {remaining_count} remaining"
            )
        active_master_data = self._active_master_data()
        master_data_by_material_id = {entry.material_id: entry for entry in active_master_data}
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
            master_data = master_data_by_material_id[release.material_id]
            requested_delivery_date = release_at.date() + timedelta(days=master_data.delivery_lead_time_min_days)
            self._validate_requested_delivery_date(release_at, requested_delivery_date, master_data)
            releases.append(
                DemandRelease(
                    case_id=f"C{index:03d}",
                    release_time=release_at,
                    material_id=release.material_id,
                    requested_delivery_date=requested_delivery_date,
                )
            )
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
        settings = self._config.run_settings.realism
        if criteria.workload_delay_multiplier_boost > settings.max_workload_delay_multiplier_boost:
            raise TraceGenerationError("workload_delay_multiplier_boost outside guardrails")
        if criteria.workload_workday_deviation_hours_boost > settings.max_workload_workday_deviation_hours_boost:
            raise TraceGenerationError("workload_workday_deviation_hours_boost outside guardrails")

    def _validate_release_inside_workday(self, release_at: datetime) -> None:
        work_start = time.fromisoformat(self._config.run_settings.working_hours.core_start)
        work_end = time.fromisoformat(self._config.run_settings.working_hours.core_end)
        if not work_start <= release_at.timetz().replace(tzinfo=None) < work_end:
            raise TraceGenerationError(f"demand release '{release_at.isoformat()}' is outside configured working hours")

    def _validate_requested_delivery_date(
        self,
        release_at: datetime,
        requested_delivery_date: date,
        master_data: MasterDataEntry,
    ) -> None:
        lead_days = (requested_delivery_date - release_at.date()).days
        if not master_data.delivery_lead_time_min_days <= lead_days <= master_data.delivery_lead_time_max_days:
            raise TraceGenerationError(
                f"requested delivery date '{requested_delivery_date.isoformat()}' outside lead-time guardrails "
                f"for material '{master_data.material_id}'"
            )
        run_end_exclusive = self._config.run_settings.run_start_date + timedelta(days=self._config.run_settings.run_horizon_days)
        earliest_payment_date = requested_delivery_date + timedelta(days=1)
        if earliest_payment_date >= run_end_exclusive:
            raise TraceGenerationError(
                f"requested delivery date '{requested_delivery_date.isoformat()}' cannot finish inside run horizon"
            )

    def _validate_pattern_lead_times(self, pattern: DemandPattern) -> None:
        pattern_date = date.fromisoformat(pattern.date)
        allowed_days = self._allowed_lead_time_days_for_pattern_date(pattern_date)
        if not allowed_days:
            raise TraceGenerationError(
                f"demand pattern date '{pattern.date}' has no lead_time_mix days that can finish inside run horizon"
            )
        for lead_time in pattern.lead_time_mix:
            if lead_time.days not in allowed_days:
                raise TraceGenerationError(
                    f"lead_time_mix days {lead_time.days} for demand pattern date '{pattern.date}' "
                    "is not allowed; use allowed_lead_time_days_by_pattern_date"
                )

    def _allowed_lead_time_days_for_pattern_date(self, pattern_date: date) -> list[int]:
        run_end = self._config.run_settings.run_start_date + timedelta(days=self._config.run_settings.run_horizon_days - 1)
        shared_days = _shared_lead_time_day_values(self._active_master_data())
        return [lead_days for lead_days in shared_days if pattern_date + timedelta(days=lead_days + 1) <= run_end]

    def _allowed_lead_time_days_by_pattern_date(self) -> dict[str, list[int]]:
        result: dict[str, list[int]] = {}
        for offset in range(self._config.run_settings.run_horizon_days):
            pattern_date = self._config.run_settings.run_start_date + timedelta(days=offset)
            allowed_days = self._allowed_lead_time_days_for_pattern_date(pattern_date)
            if allowed_days:
                result[pattern_date.isoformat()] = allowed_days
        return result

    def _sample_target_price(
        self,
        anchor: PriceAnchor,
        master_data: MasterDataEntry,
        release_at: datetime,
        index: int,
    ) -> float:
        days_since_start = (release_at.date() - self._config.run_settings.run_start_date).days
        rng = Random(f"{self._config.run_settings.scheduler_seed}:{anchor.material_id}:{release_at.isoformat()}:{index}")
        trended_anchor = anchor.anchor_price * (1 + anchor.daily_trend_pct * days_since_start)
        sampled = trended_anchor * (1 + rng.uniform(-anchor.typical_variation_pct, anchor.typical_variation_pct))
        return round(min(master_data.price_max, max(master_data.price_min, sampled)), 2)

    def _sample_target_quantity(
        self,
        profile: MaterialDemandProfile,
        master_data: MasterDataEntry,
        release_at: datetime,
        index: int,
    ) -> int:
        rng = Random(f"{self._config.run_settings.scheduler_seed}:{profile.material_id}:{release_at.isoformat()}:{index}:quantity")
        typical = profile.typical_order_quantity
        if rng.random() < profile.bulk_order_share:
            low = typical
            high = master_data.quantity_max
            mode = min(master_data.quantity_max, max(typical, int(typical + (master_data.quantity_max - typical) * 0.55)))
        else:
            spread = max(1, int(round(typical * profile.quantity_variation_pct)))
            low = max(master_data.quantity_min, typical - spread)
            high = min(master_data.quantity_max, typical + spread)
            mode = typical
        if low > high:
            low = high = typical
        sampled = int(round(rng.triangular(low, high, mode)))
        return _round_to_multiple_inside_bounds(
            sampled,
            profile.order_multiple,
            master_data.quantity_min,
            master_data.quantity_max,
        )

    def _compile_actor_day_profiles(
        self,
        actor_criteria: dict[str, ActorRealismCriteria],
        demand_patterns: list[DemandPattern],
    ) -> dict[tuple[str, str], ActorRealismCriteria]:
        workload_by_date = {pattern.date: pattern.workload_intensity for pattern in demand_patterns}
        profiles: dict[tuple[str, str], ActorRealismCriteria] = {}
        for actor in self._config.actors:
            baseline = actor_criteria[actor.id]
            guardrails = actor.realism_guardrails
            for offset in range(self._config.run_settings.run_horizon_days):
                day = self._config.run_settings.run_start_date + timedelta(days=offset)
                day_key = day.isoformat()
                workload = workload_by_date.get(day_key, "normal")
                factor = WORKLOAD_FACTORS[workload]
                rng = Random(f"{self._config.run_settings.scheduler_seed}:{actor.id}:{day_key}")
                delay_variance = abs(baseline.day_delay_multiplier_variance)
                workday_variance = abs(baseline.day_workday_deviation_hours_variance)
                pause_variance = abs(baseline.day_pause_duration_minutes_variance)
                delay_multiplier = _clamp(
                    baseline.delay_multiplier
                    + rng.uniform(-delay_variance, delay_variance)
                    + baseline.workload_delay_multiplier_boost * factor,
                    guardrails.delay_multiplier_min,
                    guardrails.delay_multiplier_max,
                )
                workday_deviation_hours = _clamp(
                    baseline.workday_deviation_hours
                    + rng.uniform(-workday_variance, workday_variance)
                    + baseline.workload_workday_deviation_hours_boost * factor,
                    guardrails.workday_deviation_hours_min,
                    guardrails.workday_deviation_hours_max,
                )
                pause_delta = (
                    rng.randint(-pause_variance, pause_variance)
                    if pause_variance > 0
                    else 0
                )
                pause_duration_minutes = int(
                    _clamp(
                        baseline.pause_duration_minutes + pause_delta,
                        guardrails.pause_duration_minutes_min,
                        guardrails.pause_duration_minutes_max,
                    )
                )
                profiles[(actor.id, day_key)] = ActorRealismCriteria(
                    actor_id=actor.id,
                    delay_multiplier=round(delay_multiplier, 3),
                    workday_deviation_hours=round(workday_deviation_hours, 3),
                    pause_duration_minutes=pause_duration_minutes,
                    day_delay_multiplier_variance=baseline.day_delay_multiplier_variance,
                    day_workday_deviation_hours_variance=baseline.day_workday_deviation_hours_variance,
                    day_pause_duration_minutes_variance=baseline.day_pause_duration_minutes_variance,
                    workload_delay_multiplier_boost=baseline.workload_delay_multiplier_boost,
                    workload_workday_deviation_hours_boost=baseline.workload_workday_deviation_hours_boost,
                )
        return profiles

    def _actor_prompt(self, actor: Actor, last_error: str | None) -> str:
        guardrails = actor.realism_guardrails
        prompt = {
            "task": "Compile actor baseline realism model for one synthetic ERP actor.",
            "output_rules": [
                "Return exactly one JSON object.",
                "Top-level keys must match required_json_shape exactly.",
                "Choose scalar values inside guardrail ranges.",
                "Day variance fields describe deterministic per-day variation bounds.",
            ],
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
                "workload_delay_multiplier_boost_max": self._config.run_settings.realism.max_workload_delay_multiplier_boost,
                "workload_workday_deviation_hours_boost_max": self._config.run_settings.realism.max_workload_workday_deviation_hours_boost,
            },
            "required_json_shape": {
                "actor_id": actor.id,
                "delay_multiplier": "number",
                "workday_deviation_hours": "number",
                "pause_duration_minutes": "integer",
                "day_delay_multiplier_variance": "number",
                "day_workday_deviation_hours_variance": "number",
                "day_pause_duration_minutes_variance": "integer",
                "workload_delay_multiplier_boost": "number",
                "workload_workday_deviation_hours_boost": "number",
            },
        }
        if last_error:
            prompt["previous_error"] = f"Validation failed: {last_error}"
        return json.dumps(prompt, sort_keys=True)

    def _price_anchor_prompt(self, last_error: str | None) -> str:
        active_master_data = self._active_master_data()
        material_ids = [item.material_id for item in active_master_data]
        prompt = {
            "task": "Compile one realistic price anchor model per configured material.",
            "output_rules": [
                "Return exactly one JSON object.",
                f"Return exactly {len(material_ids)} material_prices items.",
                "Return one material_prices item for every material_id in required_material_ids.",
                "Use the material_id strings exactly as provided; do not rename, omit, or add material ids.",
                "Do not return per-case prices.",
                "anchor_price must be inside hard price guardrails.",
            ],
            "required_material_ids": material_ids,
            "materials": [
                {
                    "material_id": item.material_id,
                    "price_min": item.price_min,
                    "price_max": item.price_max,
                    "currency": item.currency,
                }
                for item in active_master_data
            ],
            "guardrails": {
                "typical_variation_pct": [0, self._config.run_settings.realism.max_price_variation_pct],
                "daily_trend_pct": [
                    -self._config.run_settings.realism.max_daily_price_trend_pct,
                    self._config.run_settings.realism.max_daily_price_trend_pct,
                ],
            },
            "required_json_shape": {
                "material_prices": [
                    {
                        "material_id": "configured material id",
                        "anchor_price": "number",
                        "typical_variation_pct": "number",
                        "daily_trend_pct": "number",
                    }
                ]
            },
        }
        if last_error:
            prompt["previous_error"] = f"Validation failed: {last_error}"
        return json.dumps(prompt, sort_keys=True)

    def _material_demand_profile_prompt(self, last_error: str | None) -> str:
        settings = self._config.run_settings.realism
        active_master_data = self._active_master_data()
        material_ids = [item.material_id for item in active_master_data]
        prompt = {
            "task": "Compile Material Demand Profiles for active configured materials. Trace Generator normalizes relative weights and samples quantities.",
            "output_rules": [
                "Return exactly one JSON object.",
                f"Return exactly {len(material_ids)} material_profiles items.",
                "Return one material_profiles item for every material_id in required_material_ids.",
                "Use material_id strings exactly as provided; do not rename, omit, or add material ids.",
                "relative_demand_weight is a positive integer, not a probability.",
                "typical_order_quantity must be inside hard quantity guardrails.",
                "Use realistic variety: cheap consumables can have larger quantities; expensive or specialized items usually lower quantities.",
            ],
            "required_material_ids": material_ids,
            "materials": [
                {
                    "material_id": item.material_id,
                    "quantity_min": item.quantity_min,
                    "quantity_max": item.quantity_max,
                    "price_min": item.price_min,
                    "price_max": item.price_max,
                    "delivery_lead_time_min_days": item.delivery_lead_time_min_days,
                    "delivery_lead_time_max_days": item.delivery_lead_time_max_days,
                    "configured_order_multiple": item.order_multiple,
                }
                for item in active_master_data
            ],
            "guardrails": {
                "relative_demand_weight": [
                    settings.relative_demand_weight_min,
                    settings.relative_demand_weight_max,
                ],
                "quantity_variation_pct": [
                    settings.quantity_variation_pct_min,
                    settings.quantity_variation_pct_max,
                ],
                "bulk_order_share": [0, settings.max_bulk_order_share],
                "allowed_order_multiples": list(settings.allowed_order_multiples),
            },
            "required_json_shape": {
                "material_profiles": [
                    {
                        "material_id": "configured material id",
                        "relative_demand_weight": "positive integer",
                        "typical_order_quantity": "integer inside hard quantity bounds",
                        "quantity_variation_pct": "number",
                        "bulk_order_share": "number",
                        "order_multiple": "echo configured_order_multiple; the generator enforces material config",
                    }
                ]
            },
        }
        if last_error:
            prompt["previous_error"] = f"Validation failed: {last_error}"
        return json.dumps(prompt, sort_keys=True)

    def _horizon_demand_prompt(self, last_error: str | None) -> str:
        run_start = self._config.run_settings.run_start_date
        run_end = run_start + timedelta(days=self._config.run_settings.run_horizon_days - 1)
        active_master_data = self._active_master_data()
        allowed_lead_time_days = _shared_lead_time_day_values(active_master_data)
        allowed_lead_time_days_by_pattern_date = self._allowed_lead_time_days_by_pattern_date()
        example_lead_days = allowed_lead_time_days[0] if allowed_lead_time_days else _shared_lead_time_days(active_master_data)
        prompt = {
            "task": "Compile compact Demand Patterns for the whole run horizon. Do not output individual Process Cases.",
            "exact_total_case_count_required": self._config.run_settings.case_count,
            "output_rules": [
                "Return exactly one JSON object.",
                f"Sum of all pattern case_count values must equal exactly {self._config.run_settings.case_count}.",
                "Never invent a larger business volume than caseCount.",
                "Return compact daily patterns only; omit days with zero cases.",
                "Every returned pattern must have case_count >= 1.",
                "release_windows and lead_time_mix shares must each sum to 1.0.",
                "Every release window must be inside working_hours.",
                "Every release window start must be before end; do not use equal times or overnight windows.",
                "Use varied non-flat workload and release windows when realistic.",
                "Do not output material_mix; Material Demand Profiles own material assignment.",
                "Every lead_time_mix day must be valid for every configured material because material assignment happens after demand expansion.",
                "Use only days listed in allowed_lead_time_days for lead_time_mix.",
                "Every Demand Pattern date must exist in allowed_lead_time_days_by_pattern_date.",
                "For each Demand Pattern date, lead_time_mix days must come from allowed_lead_time_days_by_pattern_date[date].",
                "Do not use late horizon dates when allowed_lead_time_days_by_pattern_date has no entry for that date.",
            ],
            "caseCount": self._config.run_settings.case_count,
            "run_horizon": {
                "start_date": run_start.isoformat(),
                "end_date": run_end.isoformat(),
                "days": self._config.run_settings.run_horizon_days,
            },
            "working_hours": {
                "core_start": self._config.run_settings.working_hours.core_start,
                "core_end": self._config.run_settings.working_hours.core_end,
            },
            "materials": [
                {
                    "material_id": item.material_id,
                    "delivery_lead_time_min_days": item.delivery_lead_time_min_days,
                    "delivery_lead_time_max_days": item.delivery_lead_time_max_days,
                }
                for item in active_master_data
            ],
            "guardrails": {
                "daily_case_count": [
                    self._config.run_settings.realism.daily_case_count_min,
                    self._config.run_settings.realism.daily_case_count_max,
                ],
                "allowed_lead_time_days": allowed_lead_time_days,
            },
            "allowed_lead_time_days": allowed_lead_time_days,
            "allowed_lead_time_days_by_pattern_date": allowed_lead_time_days_by_pattern_date,
            "required_json_shape": {
                "patterns": [
                    {
                        "date": "YYYY-MM-DD",
                        "case_count": "integer",
                        "workload_intensity": "low|normal|high",
                        "release_windows": [{"start": "HH:MM", "end": "HH:MM", "share": "number"}],
                        "lead_time_mix": [{"days": "integer", "share": "number"}],
                    }
                ]
            },
            "example_json_for_current_case_count": {
                "patterns": [
                    {
                        "date": run_start.isoformat(),
                        "case_count": self._config.run_settings.case_count,
                        "workload_intensity": "normal",
                        "release_windows": [
                            {"start": self._config.run_settings.working_hours.core_start, "end": "11:00", "share": 0.5},
                            {"start": "13:00", "end": self._config.run_settings.working_hours.core_end, "share": 0.5},
                        ],
                        "lead_time_mix": [
                            {
                                "days": example_lead_days,
                                "share": 1.0,
                            }
                        ],
                    }
                ]
            },
        }
        if last_error:
            prompt["previous_error"] = f"Validation failed: {last_error}"
        return json.dumps(prompt, sort_keys=True)

    def _daily_demand_prompt(self, day: str, remaining_count: int, last_error: str | None) -> str:
        prompt = {
            "task": "Compile demand releases for one day. Output only Process Case releases, not process steps.",
            "output_rules": [
                "Return exactly one JSON object.",
                "Top-level keys must be exactly date and releases.",
                "Each release must have exactly release_time and material_id.",
                "Do not add markdown, comments, explanations, examples, nested wrappers, or extra keys.",
                "Use only allowed material ids.",
            ],
            "date": day,
            "remaining_case_count": remaining_count,
            "working_hours": {
                "core_start": self._config.run_settings.working_hours.core_start,
                "core_end": self._config.run_settings.working_hours.core_end,
            },
            "allowed_material_ids": [entry.material_id for entry in self._active_master_data()],
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
        encoded = json.dumps(
            {
                "schema_version": REALISM_COMPILER_SCHEMA_VERSION,
                "actor": actor_payload,
            },
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()[:16]


def default_realism_criteria(config: GenerationConfig) -> CompiledRealismCriteria:
    actor_criteria = {
        actor.id: ActorRealismCriteria(
            actor_id=actor.id,
            delay_multiplier=actor.delay_multiplier,
            workday_deviation_hours=0.0,
            pause_duration_minutes=config.run_settings.working_hours.pause_duration_minutes_min,
        )
        for actor in config.actors
    }
    actor_day_profiles = {
        (actor_id, (config.run_settings.run_start_date + timedelta(days=offset)).isoformat()): criteria
        for actor_id, criteria in actor_criteria.items()
        for offset in range(config.run_settings.run_horizon_days)
    }
    active_master_data = _active_master_data(config)
    demand_releases = default_demand_releases(config)
    price_anchors = {
        item.material_id: PriceAnchor(
            material_id=item.material_id,
            anchor_price=round((item.price_min + item.price_max) / 2, 2),
            typical_variation_pct=0.0,
            daily_trend_pct=0.0,
        )
        for item in active_master_data
    }
    material_demand_profiles = _default_material_demand_profiles(config)
    payload = _criteria_payload(
        actor_criteria,
        demand_releases,
        actor_day_profiles,
        price_anchors,
        material_demand_profiles,
        [],
    )
    criteria_hash = _criteria_hash(payload)
    return CompiledRealismCriteria(
        actor_criteria=actor_criteria,
        actor_day_profiles=actor_day_profiles,
        demand_releases=demand_releases,
        demand_patterns=[],
        price_anchors=price_anchors,
        material_demand_profiles=material_demand_profiles,
        criteria_hash=criteria_hash,
        llm_metadata={
            "used": False,
            "realism_criteria_hash": criteria_hash,
            "realism_compiler_schema_version": REALISM_COMPILER_SCHEMA_VERSION,
            "llm_request_count": 0,
            "llm_retry_count": 0,
            "cache_hit_count": 0,
        },
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
    active_master_data = _active_master_data(config)
    tz = ZoneInfo(config.run_settings.target_timezone)
    slot = datetime.combine(
        config.run_settings.run_start_date,
        time.fromisoformat(config.run_settings.working_hours.core_start),
        tz,
    )
    run_end_exclusive = config.run_settings.run_start_date + timedelta(days=config.run_settings.run_horizon_days)
    releases: list[DemandRelease] = []
    for index in range(1, config.run_settings.case_count + 1):
        slot = _next_default_release_slot(config, slot, run_end_exclusive, tz)
        releases.append(
            DemandRelease(
                case_id=f"C{index:03d}",
                release_time=slot,
                material_id=active_master_data[(index - 1) % len(active_master_data)].material_id,
            )
        )
        slot += timedelta(minutes=30)
    return releases


def _next_default_release_slot(
    config: GenerationConfig,
    candidate: datetime,
    run_end_exclusive: date,
    tz: ZoneInfo,
) -> datetime:
    work_start = time.fromisoformat(config.run_settings.working_hours.core_start)
    work_end = time.fromisoformat(config.run_settings.working_hours.core_end)
    current = candidate if candidate.tzinfo is not None else candidate.replace(tzinfo=tz)
    while current.date() < run_end_exclusive:
        day_start = datetime.combine(current.date(), work_start, tz)
        day_end = datetime.combine(current.date(), work_end, tz)
        if current < day_start:
            return day_start
        if current < day_end:
            return current
        current = datetime.combine(current.date() + timedelta(days=1), work_start, tz)
    raise TraceGenerationError(
        "default demand releases cannot fit caseCount into configured run horizon and working hours"
    )


def _criteria_payload(
    actor_criteria: dict[str, ActorRealismCriteria],
    demand_releases: list[DemandRelease],
    actor_day_profiles: dict[tuple[str, str], ActorRealismCriteria],
    price_anchors: dict[str, PriceAnchor],
    material_demand_profiles: dict[str, MaterialDemandProfile],
    demand_patterns: list[DemandPattern],
) -> dict:
    return {
        "schema_version": REALISM_COMPILER_SCHEMA_VERSION,
        "actor_criteria": {
            actor_id: criteria.model_dump(mode="json")
            for actor_id, criteria in sorted(actor_criteria.items())
        },
        "actor_day_profiles": {
            f"{actor_id}:{day}": criteria.model_dump(mode="json")
            for (actor_id, day), criteria in sorted(actor_day_profiles.items())
        },
        "price_anchors": {
            material_id: anchor.model_dump(mode="json")
            for material_id, anchor in sorted(price_anchors.items())
        },
        "material_demand_profiles": {
            material_id: profile.model_dump(mode="json")
            for material_id, profile in sorted(material_demand_profiles.items())
        },
        "demand_patterns": [pattern.model_dump(mode="json") for pattern in demand_patterns],
        "demand_releases": [
            {
                "case_id": release.case_id,
                "release_time": release.release_time.isoformat(),
                "material_id": release.material_id,
                "requested_delivery_date": release.requested_delivery_date.isoformat()
                if release.requested_delivery_date is not None
                else None,
                "target_price": release.target_price,
                "target_quantity": release.target_quantity,
                "workload_intensity": release.workload_intensity,
            }
            for release in demand_releases
        ],
    }


def _default_material_demand_profiles(config: GenerationConfig) -> dict[str, MaterialDemandProfile]:
    return {
        item.material_id: MaterialDemandProfile(
            material_id=item.material_id,
            relative_demand_weight=1,
            typical_order_quantity=max(item.quantity_min, min(item.quantity_max, round((item.quantity_min + item.quantity_max) / 2))),
            quantity_variation_pct=config.run_settings.realism.quantity_variation_pct_min,
            bulk_order_share=0.0,
            order_multiple=1,
        )
        for item in _active_master_data(config)
    }


def _active_master_data(config: GenerationConfig) -> tuple[MasterDataEntry, ...]:
    blocked_materials = set(config.run_settings.realism.blocked_materials)
    active = tuple(item for item in config.master_data if item.material_id not in blocked_materials)
    if not active:
        raise TraceGenerationError("No unblocked master data remains for realism compilation")
    return active


def _llm_timeout_seconds_from_env() -> int:
    raw_value = os.environ.get("REALISM_LLM_TIMEOUT_SECONDS")
    if raw_value is None:
        return 60
    try:
        timeout_seconds = int(raw_value)
    except ValueError as exc:
        raise TraceGenerationError("REALISM_LLM_TIMEOUT_SECONDS must be an integer") from exc
    if timeout_seconds < 1:
        raise TraceGenerationError("REALISM_LLM_TIMEOUT_SECONDS must be >= 1")
    return timeout_seconds


def _criteria_hash(payload: dict) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _load_json_object(raw_response: str) -> dict:
    text = _strip_json_markdown_fence(raw_response.strip())
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise TraceGenerationError(
            "Realism LLM response must be a JSON object; "
            f"got {type(payload).__name__}: {_preview_text(text)}"
        )
    return payload


def _preview_text(value: str, *, limit: int = 240) -> str:
    compact = " ".join(value.split())
    if len(compact) <= limit:
        return compact
    return f"{compact[:limit]}..."


def _normalize_actor_payload(payload: dict, actor: Actor) -> dict:
    allowed_keys = set(ActorRealismCriteria.model_fields)
    required_keys = {
        "actor_id",
        "delay_multiplier",
        "workday_deviation_hours",
        "pause_duration_minutes",
    }
    if required_keys.issubset(payload):
        return {key: value for key, value in payload.items() if key in allowed_keys}
    nested = payload.get("synthetic_data")
    if isinstance(nested, dict):
        normalized = {key: value for key, value in nested.items() if key in allowed_keys}
        actor_payload = payload.get("actor")
        if isinstance(actor_payload, dict):
            normalized["actor_id"] = actor_payload.get("actor_id", actor.id)
        else:
            normalized["actor_id"] = payload.get("actor_id", actor.id)
        return normalized
    actor_payload = payload.get("actor")
    guardrail_payload = payload.get("guardrails")
    if isinstance(actor_payload, dict) and isinstance(guardrail_payload, dict):
        normalized = {key: value for key, value in payload.items() if key in allowed_keys}
        normalized.update({key: value for key, value in guardrail_payload.items() if key in allowed_keys})
        normalized["actor_id"] = actor_payload.get("actor_id", actor.id)
        return normalized
    return payload


def _strip_json_markdown_fence(text: str) -> str:
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if not lines:
        return text
    first_line = lines[0].strip().lower()
    if first_line not in {"```", "```json"}:
        return text
    if len(lines) >= 2 and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return text


def _validate_share_sum(shares: list[float], name: str) -> None:
    if not shares or any(share < 0 for share in shares):
        raise TraceGenerationError(f"{name} shares must be non-negative and non-empty")
    if abs(sum(shares) - 1.0) > 0.0001:
        raise TraceGenerationError(f"{name} shares must sum to 1.0")


def _allocate_counts(total: int, shares: list[float]) -> list[int]:
    raw = [total * share for share in shares]
    counts = [int(value) for value in raw]
    remainder = total - sum(counts)
    order = sorted(range(len(shares)), key=lambda index: raw[index] - counts[index], reverse=True)
    for index in order[:remainder]:
        counts[index] += 1
    return counts


def _expand_weighted_values(total: int, weighted_values: list[tuple[object, float]]) -> list:
    counts = _allocate_counts(total, [share for _value, share in weighted_values])
    values: list = []
    for (value, _share), count in zip(weighted_values, counts, strict=True):
        values.extend([value] * count)
    return values


def _material_ids_for_releases(
    total: int,
    profiles: list[MaterialDemandProfile],
    *,
    seed: int,
    max_share: float | None,
) -> list[str]:
    counts = _allocate_material_counts(total, [profile.relative_demand_weight for profile in profiles])
    if max_share is not None:
        for profile, count in zip(profiles, counts, strict=True):
            if total > 0 and count / total > max_share:
                raise TraceGenerationError(
                    f"material '{profile.material_id}' allocated share {count / total:.3f} exceeds maxMaterialSharePerHorizon {max_share}"
                )
    material_ids: list[str] = []
    for profile, count in zip(profiles, counts, strict=True):
        material_ids.extend([profile.material_id] * count)
    rng = Random(f"{seed}:material-demand-profile:{total}:{[(profile.material_id, profile.relative_demand_weight) for profile in profiles]}")
    rng.shuffle(material_ids)
    return material_ids


def _allocate_material_counts(total: int, weights: list[int]) -> list[int]:
    weight_sum = sum(weights)
    if weight_sum <= 0:
        raise TraceGenerationError("material demand profile weights must sum to a positive value")
    counts = _allocate_counts(total, [weight / weight_sum for weight in weights])
    if total >= len(weights):
        zero_indices = [index for index, count in enumerate(counts) if count == 0]
        for zero_index in zero_indices:
            donor_indices = sorted(
                [index for index, count in enumerate(counts) if count > 1],
                key=lambda index: (counts[index], weights[index]),
                reverse=True,
            )
            if not donor_indices:
                break
            donor_index = donor_indices[0]
            counts[donor_index] -= 1
            counts[zero_index] = 1
    return counts


def _round_to_multiple_inside_bounds(value: int, multiple: int, minimum: int, maximum: int) -> int:
    if multiple <= 1:
        return max(minimum, min(maximum, value))
    lower = ((minimum + multiple - 1) // multiple) * multiple
    upper = (maximum // multiple) * multiple
    if lower > upper:
        return max(minimum, min(maximum, value))
    rounded = round(value / multiple) * multiple
    return max(lower, min(upper, rounded))


def _shared_lead_time_days(master_data: tuple[MasterDataEntry, ...]) -> int:
    earliest = max(item.delivery_lead_time_min_days for item in master_data)
    latest = min(item.delivery_lead_time_max_days for item in master_data)
    if earliest <= latest:
        return earliest
    return master_data[0].delivery_lead_time_min_days


def _shared_lead_time_day_values(master_data: tuple[MasterDataEntry, ...]) -> list[int]:
    earliest = max(item.delivery_lead_time_min_days for item in master_data)
    latest = min(item.delivery_lead_time_max_days for item in master_data)
    if earliest > latest:
        return []
    return list(range(earliest, latest + 1))


def _release_times_for_window(
    target_day: date,
    window: ReleaseWindow,
    count: int,
    tz: ZoneInfo,
    seed: int,
) -> list[datetime]:
    if count == 0:
        return []
    start_dt = datetime.combine(target_day, time.fromisoformat(window.start), tz)
    end_dt = datetime.combine(target_day, time.fromisoformat(window.end), tz)
    seconds = max(1, int((end_dt - start_dt).total_seconds()))
    rng = Random(f"{seed}:{target_day.isoformat()}:{window.start}:{window.end}:{count}")
    return [
        start_dt + timedelta(seconds=min(seconds - 1, int(seconds * ((index + rng.random()) / count))))
        for index in range(count)
    ]


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return min(maximum, max(minimum, value))
