"""Case, input, and wave planning."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from random import Random

from erp_trace_generator.bindings import business_dates_for_step, resolve_step_inputs
from erp_trace_generator.models import Actor, CasePlan, GenerationConfig, PlannedNode, TechnicalUser
from erp_trace_generator.timeline import TimelinePlanner


def plan_cases(config: GenerationConfig, rng: Random) -> list[CasePlan]:
    cases: list[CasePlan] = []
    for index in range(1, config.run_settings.case_count + 1):
        master_data = rng.choice(config.master_data)
        quantity = rng.randint(master_data.quantity_min, master_data.quantity_max)
        target_price = round(rng.uniform(master_data.price_min, master_data.price_max), 2)
        delivery_days = rng.randint(
            master_data.delivery_lead_time_min_days,
            master_data.delivery_lead_time_max_days,
        )
        storage_location = rng.choice(master_data.valid_storage_locations)
        cases.append(
            CasePlan(
                case_id=f"C{index:03d}",
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
                delivery_date=config.run_settings.run_start_date + timedelta(days=delivery_days),
                gross_amount=round(quantity * target_price, 2),
            )
        )
    return cases


def plan_nodes(config: GenerationConfig, cases: list[CasePlan], rng: Random) -> list[PlannedNode]:
    process = config.active_process()
    timeline = TimelinePlanner(config.run_settings, rng)
    actor_available: dict[str, datetime] = defaultdict(timeline.first_start)
    technical_user_available: dict[str, datetime] = defaultdict(timeline.first_start)
    nodes: list[PlannedNode] = []

    for case in cases:
        previous_node: PlannedNode | None = None
        for step in process.steps:
            if previous_node is None:
                earliest = timeline.first_start()
            else:
                earliest = timeline.add_inter_step_delay(previous_node.target_end, previous_node.step_type, step.step_type)
            actor, technical_user, start = _allocate_actor(
                config=config,
                process_type=process.process_type,
                step_type=step.step_type,
                earliest=earliest,
                actor_available=actor_available,
                technical_user_available=technical_user_available,
                timeline=timeline,
            )
            end = timeline.add_step_duration(start, step.step_type, actor.speed_factor)
            actor_available[actor.id] = end
            technical_user_available[technical_user.id] = end

            node = PlannedNode(
                node_id=f"{case.case_id}_{step.step_id}",
                case_id=case.case_id,
                step_id=step.step_id,
                step_type=step.step_type,
                tool_name=step.tool_name,
                virtual_actor_id=actor.id,
                technical_user_id=technical_user.id,
                session_id=f"{actor.id}-session",
                inputs=resolve_step_inputs(step, case),
                expected_outputs=list(step.expected_outputs),
                business_dates=business_dates_for_step(step, case),
                target_start=start,
                target_end=end,
            )
            nodes.append(node)
            previous_node = node
    return nodes


def _allocate_actor(
    *,
    config: GenerationConfig,
    process_type: str,
    step_type: str,
    earliest: datetime,
    actor_available: dict[str, datetime],
    technical_user_available: dict[str, datetime],
    timeline: TimelinePlanner,
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
            )
        )
        candidates.append((start, actor_index, actor, technical_user))

    start, _actor_index, actor, technical_user = min(candidates, key=lambda item: (item[0], item[1]))
    return actor, technical_user, start


def plan_waves(config: GenerationConfig, nodes: list[PlannedNode]) -> list[dict]:
    process = config.active_process()
    step_rank = {step.step_type: index for index, step in enumerate(process.steps)}
    unscheduled = sorted(nodes, key=lambda node: (node.target_start, step_rank[node.step_type], node.case_id))
    scheduled: set[str] = set()
    dependencies = {
        (node.case_id, dep.to_step_type): f"{node.case_id}_{_step_id_for(process, dep.from_step_type)}"
        for node in nodes
        for dep in process.dependencies
    }
    waves: list[dict] = []

    while unscheduled:
        used_actors: set[str] = set()
        used_technical_users: set[str] = set()
        wave_nodes: list[PlannedNode] = []

        ready_nodes = []
        for node in unscheduled:
            required_parent = dependencies.get((node.case_id, node.step_type))
            if required_parent is not None and required_parent not in scheduled:
                continue
            ready_nodes.append(node)

        for node in sorted(ready_nodes, key=lambda item: (item.target_start, step_rank[item.step_type], item.case_id)):
            if node.virtual_actor_id in used_actors or node.technical_user_id in used_technical_users:
                continue
            if len(wave_nodes) >= config.run_settings.max_parallel_sessions:
                continue
            wave_nodes.append(node)
            used_actors.add(node.virtual_actor_id)
            used_technical_users.add(node.technical_user_id)

        if not wave_nodes:
            raise AssertionError("scheduler validation missed impossible schedule")

        for node in wave_nodes:
            unscheduled.remove(node)
            scheduled.add(node.node_id)

        waves.append(
            {
                "wave_id": f"W{len(waves) + 1:03d}",
                "sequence_no": len(waves) + 1,
                "nodes": [
                    {"node_id": node.node_id, "startup_order": index}
                    for index, node in enumerate(wave_nodes, start=1)
                ],
            }
        )

    return waves


def align_node_times_to_waves(nodes: list[PlannedNode], waves: list[dict]) -> None:
    """Shift later waves forward when wave barriers would otherwise invert planned time."""

    nodes_by_id = {node.node_id: node for node in nodes}
    wave_floor: datetime | None = None
    for wave in waves:
        wave_nodes = [
            nodes_by_id[item["node_id"]]
            for item in sorted(wave["nodes"], key=lambda value: value["startup_order"])
        ]
        if wave_floor is not None:
            for node in wave_nodes:
                if node.target_start < wave_floor:
                    duration = node.target_end - node.target_start
                    node.target_start = wave_floor
                    node.target_end = wave_floor + duration
        wave_floor = max(node.target_end for node in wave_nodes)


def _step_id_for(process, step_type: str) -> str:
    return next(step.step_id for step in process.steps if step.step_type == step_type)
