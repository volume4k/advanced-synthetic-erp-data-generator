"""Case, input, and wave planning."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, time, timedelta
from random import Random
from zoneinfo import ZoneInfo

from erp_trace_generator.bindings import planned_date_inputs_for_step, resolve_step_inputs
from erp_trace_generator.errors import TraceGenerationError
from erp_trace_generator.models import Actor, CasePlan, GenerationConfig, PlannedStep, ProcessStep, TechnicalUser
from erp_trace_generator.realism import ActorRealismCriteria, DemandRelease, default_demand_releases
from erp_trace_generator.timeline import TimelinePlanner


def plan_cases(config: GenerationConfig, rng: Random, *, demand_releases: list[DemandRelease] | None = None) -> list[CasePlan]:
    releases = demand_releases or default_demand_releases(config)
    if len(releases) != config.run_settings.case_count:
        raise ValueError("demand_releases must match configured case_count")
    cases: list[CasePlan] = []
    for index, release in enumerate(releases, start=1):
        master_data = _master_data_for_release(config, release, rng)
        quantity = rng.randint(master_data.quantity_min, master_data.quantity_max)
        target_price = round(rng.uniform(master_data.price_min, master_data.price_max), 2)
        delivery_days = rng.randint(
            master_data.delivery_lead_time_min_days,
            master_data.delivery_lead_time_max_days,
        )
        storage_location = rng.choice(master_data.valid_storage_locations)
        cases.append(
            CasePlan(
                case_id=release.case_id or f"C{index:03d}",
                process_type=config.active_process().process_type,
                material_id=master_data.material_id,
                vendor_id=rng.choice(master_data.valid_vendors),
                plant=rng.choice(master_data.valid_plants),
                purchasing_org=rng.choice(master_data.valid_purchasing_orgs),
                storage_location=storage_location,
                storage_location_label=config.run_settings.storage_location_labels.get(storage_location, storage_location),
                quantity=quantity,
                target_price=target_price,
                currency=master_data.currency,
                delivery_date=release.release_time.date() + timedelta(days=delivery_days),
                gross_amount=round(quantity * target_price, 2),
                demand_release_time=release.release_time,
            )
        )
    return cases


def plan_steps(
    config: GenerationConfig,
    cases: list[CasePlan],
    rng: Random,
    *,
    actor_criteria: dict[str, ActorRealismCriteria] | None = None,
) -> list[PlannedStep]:
    process = config.active_process()
    timeline = TimelinePlanner(config.run_settings, rng)
    actor_available: dict[str, datetime] = defaultdict(timeline.first_start)
    technical_user_available: dict[str, datetime] = defaultdict(timeline.first_start)
    planned_steps: list[PlannedStep] = []
    criteria = actor_criteria or _default_actor_criteria(config)
    next_step_index = {case.case_id: 0 for case in cases}
    earliest_by_case = {
        case.case_id: case.demand_release_time or timeline.first_start()
        for case in cases
    }
    case_by_id = {case.case_id: case for case in cases}

    while True:
        candidates: list[tuple[datetime, int, str, ProcessStep, Actor, TechnicalUser]] = []
        for case in cases:
            step_index = next_step_index[case.case_id]
            if step_index >= len(process.steps):
                continue
            step = process.steps[step_index]
            earliest = max(earliest_by_case[case.case_id], _business_date_gate(config, case, step.step_type))
            actor, technical_user, start = _allocate_actor(
                config=config,
                process_type=process.process_type,
                step_type=step.step_type,
                earliest=earliest,
                actor_available=actor_available,
                technical_user_available=technical_user_available,
                timeline=timeline,
                actor_criteria=criteria,
            )
            candidates.append((start, step_index, case.case_id, step, actor, technical_user))

        if not candidates:
            break

        start, _step_index, case_id, step, actor, technical_user = min(
            candidates,
            key=lambda item: (item[0], item[1], item[2]),
        )
        case = case_by_id[case_id]
        actor_realism = criteria[actor.id]
        end = timeline.add_step_duration(start, step.step_type, actor_realism.delay_multiplier, actor_realism)
        actor_available[actor.id] = end
        technical_user_available[technical_user.id] = end

        planned_step = PlannedStep(
            planned_step_id=f"{case.case_id}_{step.step_id}",
            case_id=case.case_id,
            step_id=step.step_id,
            step_type=step.step_type,
            tool_name=step.tool_name,
            synthetic_actor_id=actor.id,
            technical_sap_user_id=technical_user.id,
            actor_session_id=f"{actor.id}-session",
            inputs=resolve_step_inputs(step, case),
            required_sap_object_keys=list(step.required_sap_object_keys),
            planned_date_inputs=planned_date_inputs_for_step(step, case),
            target_start=start,
            target_end=end,
        )
        planned_steps.append(planned_step)
        next_step_index[case.case_id] += 1
        if next_step_index[case.case_id] < len(process.steps):
            next_step = process.steps[next_step_index[case.case_id]]
            earliest_by_case[case.case_id] = timeline.add_inter_step_delay(
                end,
                planned_step.step_type,
                next_step.step_type,
            )
    return planned_steps


def _allocate_actor(
    *,
    config: GenerationConfig,
    process_type: str,
    step_type: str,
    earliest: datetime,
    actor_available: dict[str, datetime],
    technical_user_available: dict[str, datetime],
    timeline: TimelinePlanner,
    actor_criteria: dict[str, ActorRealismCriteria],
) -> tuple[Actor, TechnicalUser, datetime]:
    candidates: list[tuple[datetime, int, Actor, TechnicalUser]] = []
    capable_actors = set(config.actors_capable_of(process_type, step_type))
    for actor_index, actor in enumerate(config.actors):
        if actor not in capable_actors:
            continue
        technical_user = config.technical_user_for_actor(actor.id)
        start = timeline.align_start(
            max(
                earliest,
                actor_available[actor.id],
                technical_user_available[technical_user.id],
            ),
            actor_criteria[actor.id],
        )
        candidates.append((start, actor_index, actor, technical_user))

    start, _actor_index, actor, technical_user = min(candidates, key=lambda item: (item[0], item[1]))
    return actor, technical_user, start


def plan_waves(config: GenerationConfig, planned_steps: list[PlannedStep]) -> list[dict]:
    process = config.active_process()
    step_rank = {step.step_type: index for index, step in enumerate(process.steps)}
    unscheduled = sorted(
        planned_steps,
        key=lambda planned_step: (planned_step.target_start, step_rank[planned_step.step_type], planned_step.case_id),
    )
    scheduled: set[str] = set()
    dependencies: dict[tuple[str, str], set[str]] = defaultdict(set)
    case_ids = {planned_step.case_id for planned_step in planned_steps}
    for case_id in case_ids:
        for dep in process.dependencies:
            dependencies[(case_id, dep.to_step_type)].add(f"{case_id}_{_step_id_for(process, dep.from_step_type)}")
    waves: list[dict] = []

    while unscheduled:
        used_actors: set[str] = set()
        used_technical_users: set[str] = set()
        wave_steps: list[PlannedStep] = []

        ready_steps = []
        for planned_step in unscheduled:
            required_parents = dependencies.get((planned_step.case_id, planned_step.step_type), set())
            if not required_parents.issubset(scheduled):
                continue
            ready_steps.append(planned_step)

        for planned_step in sorted(
            ready_steps,
            key=lambda item: (item.target_start, step_rank[item.step_type], item.case_id),
        ):
            if planned_step.synthetic_actor_id in used_actors or planned_step.technical_sap_user_id in used_technical_users:
                continue
            if len(wave_steps) >= config.run_settings.max_parallel_actor_sessions:
                continue
            wave_steps.append(planned_step)
            used_actors.add(planned_step.synthetic_actor_id)
            used_technical_users.add(planned_step.technical_sap_user_id)

        if not wave_steps:
            raise AssertionError("scheduler validation missed impossible schedule")

        for planned_step in wave_steps:
            unscheduled.remove(planned_step)
            scheduled.add(planned_step.planned_step_id)

        waves.append(
            {
                "wave_id": f"W{len(waves) + 1:03d}",
                "sequence_no": len(waves) + 1,
                "planned_steps": [
                    {"planned_step_id": planned_step.planned_step_id, "startup_order": index}
                    for index, planned_step in enumerate(wave_steps, start=1)
                ],
            }
        )

    return waves


def align_planned_step_times_to_waves(planned_steps: list[PlannedStep], waves: list[dict]) -> None:
    """Shift later waves forward when wave barriers would otherwise invert planned time."""

    planned_steps_by_id = {planned_step.planned_step_id: planned_step for planned_step in planned_steps}
    wave_floor: datetime | None = None
    for wave in waves:
        wave_steps = [
            planned_steps_by_id[item["planned_step_id"]]
            for item in sorted(wave["planned_steps"], key=lambda value: value["startup_order"])
        ]
        if wave_floor is not None:
            for planned_step in wave_steps:
                if planned_step.target_start < wave_floor:
                    duration = planned_step.target_end - planned_step.target_start
                    planned_step.target_start = wave_floor
                    planned_step.target_end = wave_floor + duration
        wave_floor = max(planned_step.target_end for planned_step in wave_steps)


def _step_id_for(process, step_type: str) -> str:
    return next(step.step_id for step in process.steps if step.step_type == step_type)


def _master_data_for_release(config: GenerationConfig, release: DemandRelease, rng: Random):
    if release.material_id:
        match = next((item for item in config.master_data if item.material_id == release.material_id), None)
        if match is None:
            raise TraceGenerationError(f"Demand release references unknown material_id '{release.material_id}'")
        return match
    return rng.choice(config.master_data)


def _default_actor_criteria(config: GenerationConfig) -> dict[str, ActorRealismCriteria]:
    return {
        actor.id: ActorRealismCriteria(
            actor_id=actor.id,
            delay_multiplier=actor.delay_multiplier,
            workday_deviation_hours=0.0,
            pause_duration_minutes=config.run_settings.working_hours.pause_duration_minutes_min,
            runtime_delay_cap_seconds=actor.runtime_delay_cap_seconds,
        )
        for actor in config.actors
    }


def _business_date_gate(config: GenerationConfig, case: CasePlan, step_type: str) -> datetime:
    tz = ZoneInfo(config.run_settings.target_timezone)
    work_start = time.fromisoformat(config.run_settings.working_hours.core_start)
    if step_type in {"post_goods_receipt", "enter_incoming_invoice"}:
        return datetime.combine(case.delivery_date, work_start, tz)
    if step_type == "post_outgoing_payment":
        payment_date = case.delivery_date + timedelta(days=1)
        return datetime.combine(payment_date, work_start, tz)
    return datetime.min.replace(tzinfo=tz)
