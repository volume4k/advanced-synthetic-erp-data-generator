from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from erp_trace_generator.config import load_generation_config
from erp_trace_generator.errors import TraceGenerationError
from erp_trace_generator.generator import generate_trace_artifacts
from erp_trace_generator.realism import RealismCompiler, RealismLLMClient


class FakeRealismClient(RealismLLMClient):
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.prompts: list[str] = []

    def complete_json(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if not self.responses:
            raise AssertionError("unexpected LLM call")
        return self.responses.pop(0)


def test_realism_compiler_accepts_valid_actor_criteria(tmp_path: Path) -> None:
    config = _load_config(tmp_path, _base_config())
    response = json.dumps(
        {
            "actor_id": "procurement_01",
            "delay_multiplier": 1.2,
            "workday_deviation_hours": -0.5,
            "pause_duration_minutes": 45,
            "runtime_delay_cap_seconds": 4.0,
        }
    )
    client = FakeRealismClient([response])

    criteria = RealismCompiler(config=config, client=client, cache_dir=tmp_path).compile_actor("procurement_01")

    assert criteria.actor_id == "procurement_01"
    assert criteria.delay_multiplier == 1.2
    assert criteria.pause_duration_minutes == 45
    assert len(client.prompts) == 1


def test_realism_compiler_retries_invalid_actor_criteria_with_error_feedback(tmp_path: Path) -> None:
    config = _load_config(tmp_path, _base_config())
    invalid = json.dumps(
        {
            "actor_id": "procurement_01",
            "delay_multiplier": 9.0,
            "workday_deviation_hours": -0.5,
            "pause_duration_minutes": 45,
            "runtime_delay_cap_seconds": 4.0,
        }
    )
    valid = json.dumps(
        {
            "actor_id": "procurement_01",
            "delay_multiplier": 1.1,
            "workday_deviation_hours": -0.5,
            "pause_duration_minutes": 45,
            "runtime_delay_cap_seconds": 4.0,
        }
    )
    client = FakeRealismClient([invalid, valid])

    criteria = RealismCompiler(config=config, client=client, cache_dir=tmp_path, max_retries=2).compile_actor(
        "procurement_01"
    )

    assert criteria.delay_multiplier == 1.1
    assert len(client.prompts) == 2
    assert "Validation failed" in client.prompts[1]


def test_realism_compiler_fails_after_invalid_actor_retries(tmp_path: Path) -> None:
    config = _load_config(tmp_path, _base_config())
    invalid = json.dumps(
        {
            "actor_id": "procurement_01",
            "delay_multiplier": 9.0,
            "workday_deviation_hours": -0.5,
            "pause_duration_minutes": 45,
            "runtime_delay_cap_seconds": 4.0,
        }
    )
    client = FakeRealismClient([invalid, invalid])

    with pytest.raises(TraceGenerationError, match="Could not compile realism criteria for actor 'procurement_01'"):
        RealismCompiler(config=config, client=client, cache_dir=tmp_path, max_retries=2).compile_actor("procurement_01")


def test_realism_compiler_uses_cached_actor_criteria(tmp_path: Path) -> None:
    config = _load_config(tmp_path, _base_config())
    cached = {
        "actor_id": "procurement_01",
        "delay_multiplier": 1.15,
        "workday_deviation_hours": 0.0,
        "pause_duration_minutes": 40,
        "runtime_delay_cap_seconds": 3.0,
    }
    cache_path = RealismCompiler(config=config, client=FakeRealismClient([]), cache_dir=tmp_path).actor_cache_path(
        "procurement_01"
    )
    cache_path.write_text(json.dumps(cached), encoding="utf-8")
    client = FakeRealismClient([])

    criteria = RealismCompiler(config=config, client=client, cache_dir=tmp_path).compile_actor("procurement_01")

    assert criteria.delay_multiplier == 1.15
    assert client.prompts == []


def test_realism_compiler_accepts_daily_demand_releases(tmp_path: Path) -> None:
    config = _load_config(tmp_path, _base_config())
    client = FakeRealismClient(
        [
            json.dumps(
                {
                    "date": "2026-05-18",
                    "releases": [
                        {"release_time": "08:10", "material_id": "MA025"},
                        {"release_time": "08:40", "material_id": "MA025"},
                    ],
                }
            )
        ]
    )

    releases = RealismCompiler(config=config, client=client, cache_dir=tmp_path).compile_daily_demand("2026-05-18", 2)

    assert [release.case_id for release in releases] == ["C001", "C002"]
    assert releases[0].release_time.isoformat() == "2026-05-18T08:10:00+02:00"
    assert releases[1].material_id == "MA025"


def test_realism_compiler_rejects_unknown_demand_material(tmp_path: Path) -> None:
    config = _load_config(tmp_path, _base_config())
    client = FakeRealismClient(
        [
            json.dumps(
                {
                    "date": "2026-05-18",
                    "releases": [{"release_time": "08:10", "material_id": "MISSING"}],
                }
            )
        ]
    )

    with pytest.raises(TraceGenerationError, match="unknown material_id 'MISSING'"):
        RealismCompiler(config=config, client=client, cache_dir=tmp_path, max_retries=1).compile_daily_demand(
            "2026-05-18", 1
        )


def test_realism_compiler_rejects_demand_release_that_cannot_finish_in_horizon(tmp_path: Path) -> None:
    payload = _base_config()
    payload["runSettings"]["runHorizonDays"] = 3
    payload["masterData"][0]["deliveryLeadTimeMinDays"] = 5
    payload["masterData"][0]["deliveryLeadTimeMaxDays"] = 5
    config = _load_config(tmp_path, payload)
    client = FakeRealismClient(
        [
            json.dumps(
                {
                    "date": "2026-05-18",
                    "releases": [{"release_time": "08:10", "material_id": "MA025"}],
                }
            )
        ]
    )

    with pytest.raises(TraceGenerationError, match="cannot finish inside run horizon"):
        RealismCompiler(config=config, client=client, cache_dir=tmp_path, max_retries=1).compile_daily_demand(
            "2026-05-18", 1
        )


def test_trace_generation_uses_enabled_realism_compiler(tmp_path: Path) -> None:
    payload = _base_config()
    payload["runSettings"]["realism"] = {"enabled": True, "maxRetries": 1}
    config_path = tmp_path / "main.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    client = FakeRealismClient(
        [
            _actor_response("procurement_01", 1.2),
            _actor_response("warehouse_01", 1.4),
            _actor_response("accounts_payable_01", 1.0),
            json.dumps(
                {
                    "date": "2026-05-18",
                    "releases": [
                        {"release_time": "08:05", "material_id": "MA025"},
                        {"release_time": "08:35", "material_id": "MA025"},
                    ],
                }
            ),
        ]
    )

    artifacts = generate_trace_artifacts(
        config_path=config_path,
        out_dir=tmp_path / "out",
        run_id="RUN_REALISM",
        seed=17,
        realism_client=client,
        realism_cache_dir=tmp_path / "cache",
    )
    trace = yaml.safe_load(artifacts.execution_trace_path.read_text(encoding="utf-8"))

    assert trace["llm_metadata"]["used"] is True
    assert trace["llm_metadata"]["realism_criteria_hash"]
    assert trace["actor_sessions"][0]["human_delay_profile"] == {
        "delay_multiplier": 1.2,
        "runtime_delay_cap_seconds": 4.0,
    }
    first_step = trace["dependency_graph"]["planned_steps"][0]
    assert first_step["case_id"] == "C001"
    assert first_step["planned_synthetic_time"]["start"].startswith("2026-05-18T08:05:00")
    assert len(client.prompts) == 4


def _load_config(tmp_path: Path, payload: dict):
    config_path = tmp_path / "main.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return load_generation_config(config_path)


def _base_config() -> dict:
    from test_trace_generation import _base_config as build_base_config

    payload = build_base_config()
    payload["runSettings"]["runHorizonDays"] = 10
    return payload


def _actor_response(actor_id: str, delay_multiplier: float) -> str:
    return json.dumps(
        {
            "actor_id": actor_id,
            "delay_multiplier": delay_multiplier,
            "workday_deviation_hours": 0.0,
            "pause_duration_minutes": 45,
            "runtime_delay_cap_seconds": 4.0,
        }
    )
