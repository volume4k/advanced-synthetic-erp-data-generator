from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import yaml

from erp_trace_generator import realism as realism_module
from erp_trace_generator.cli import main
from erp_trace_generator.config import load_generation_config
from erp_trace_generator.env import load_env_file, read_env_values
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


def test_realism_compiler_accepts_json_markdown_fenced_actor_criteria(tmp_path: Path) -> None:
    config = _load_config(tmp_path, _base_config())
    response = """```json
{
  "actor_id": "procurement_01",
  "delay_multiplier": 1.2,
  "workday_deviation_hours": -0.5,
  "pause_duration_minutes": 45,
  "runtime_delay_cap_seconds": 4.0
}
```"""
    client = FakeRealismClient([response])

    criteria = RealismCompiler(config=config, client=client, cache_dir=tmp_path).compile_actor("procurement_01")

    assert criteria.actor_id == "procurement_01"
    assert criteria.delay_multiplier == 1.2


def test_realism_compiler_accepts_local_model_actor_wrapper(tmp_path: Path) -> None:
    config = _load_config(tmp_path, _base_config())
    response = json.dumps(
        {
            "actor": {"actor_id": "procurement_01", "role": "procurement"},
            "synthetic_data": {
                "delay_multiplier": 1.2,
                "workday_deviation_hours": -0.5,
                "pause_duration_minutes": 45,
                "runtime_delay_cap_seconds": 4.0,
                "day_delay_multiplier_variance": 0.1,
                "day_workday_deviation_hours_variance": 0.2,
                "day_pause_duration_minutes_variance": 10,
                "workload_delay_multiplier_boost": 0.15,
                "workload_workday_deviation_hours_boost": 0.25,
            },
            "ignored_wrapper_key": "local model metadata",
        }
    )
    client = FakeRealismClient([response])

    criteria = RealismCompiler(config=config, client=client, cache_dir=tmp_path).compile_actor("procurement_01")

    assert criteria.actor_id == "procurement_01"
    assert criteria.delay_multiplier == 1.2
    assert criteria.workload_delay_multiplier_boost == 0.15


def test_realism_compiler_accepts_local_model_actor_with_extra_input_echo(tmp_path: Path) -> None:
    config = _load_config(tmp_path, _base_config())
    response = json.dumps(
        {
            "actor": {"actor_id": "procurement_01", "role": "procurement"},
            "guardrails": {"delay_multiplier": 1.2, "pause_duration_minutes": 45},
            "delay_multiplier": 1.2,
            "workday_deviation_hours": -0.5,
            "pause_duration_minutes": 45,
            "runtime_delay_cap_seconds": 4.0,
            "day_delay_multiplier_variance": 0.1,
            "day_workday_deviation_hours_variance": -0.2,
            "day_pause_duration_minutes_variance": 10,
            "workload_delay_multiplier_boost": 0.15,
            "workload_workday_deviation_hours_boost": 0.25,
            "output_rules": None,
        }
    )
    client = FakeRealismClient([response])

    criteria = RealismCompiler(config=config, client=client, cache_dir=tmp_path).compile_actor("procurement_01")

    assert criteria.actor_id == "procurement_01"
    assert criteria.workday_deviation_hours == -0.5


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


def test_realism_compiler_accepts_json_markdown_fenced_daily_demand(tmp_path: Path) -> None:
    config = _load_config(tmp_path, _base_config())
    client = FakeRealismClient(
        [
            """```json
{
  "date": "2026-05-18",
  "releases": [
    {"release_time": "08:10", "material_id": "MA025"}
  ]
}
```"""
        ]
    )

    releases = RealismCompiler(config=config, client=client, cache_dir=tmp_path).compile_daily_demand("2026-05-18", 1)

    assert [release.case_id for release in releases] == ["C001"]
    assert releases[0].material_id == "MA025"


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
            _price_anchor_response("MA025", 20.0),
            json.dumps(
                {
                    "patterns": [
                        {
                            "date": "2026-05-18",
                            "case_count": 2,
                            "workload_intensity": "normal",
                            "release_windows": [{"start": "08:05", "end": "08:45", "share": 1.0}],
                            "lead_time_mix": [{"days": 5, "share": 1.0}],
                            "material_mix": [{"material_id": "MA025", "share": 1.0}],
                        }
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
    assert trace["llm_metadata"]["realism_compiler_schema_version"] == "2"
    assert trace["llm_metadata"]["llm_request_count"] == 5
    assert trace["llm_metadata"]["llm_retry_count"] == 0
    assert trace["actor_sessions"][0]["human_delay_profile"] == {
        "delay_multiplier": 1.2,
        "runtime_delay_cap_seconds": 4.0,
    }
    assert trace["cases"][0]["requested_delivery_date"] == "2026-05-23"
    assert 19.0 <= trace["cases"][0]["line_items"][0]["target_price"] <= 21.0
    first_step = trace["dependency_graph"]["planned_steps"][0]
    assert first_step["case_id"] == "C001"
    first_start = first_step["planned_synthetic_time"]["start"]
    assert first_start.startswith("2026-05-18T08:")
    assert "08:05:00" <= first_start[11:19] < "08:45:00"
    assert first_step["inputs"]["delivery_date"] == "05/23/2026"
    assert len(client.prompts) == 5


def test_realism_compiler_expands_horizon_patterns_without_per_case_llm_calls(tmp_path: Path) -> None:
    payload = _base_config()
    payload["runSettings"]["caseCount"] = 10_000
    payload["runSettings"]["runHorizonDays"] = 30
    config = _load_config(tmp_path, payload)
    client = FakeRealismClient(
        [
            _actor_response("procurement_01", 1.2),
            _actor_response("warehouse_01", 1.4),
            _actor_response("accounts_payable_01", 1.0),
            _price_anchor_response("MA025", 20.0),
            json.dumps(
                {
                    "patterns": [
                        {
                            "date": "2026-05-18",
                            "case_count": 6000,
                            "workload_intensity": "high",
                            "release_windows": [
                                {"start": "08:00", "end": "10:30", "share": 0.45},
                                {"start": "13:00", "end": "16:30", "share": 0.55},
                            ],
                            "lead_time_mix": [{"days": 5, "share": 1.0}],
                            "material_mix": [{"material_id": "MA025", "share": 1.0}],
                        },
                        {
                            "date": "2026-05-19",
                            "case_count": 4000,
                            "workload_intensity": "normal",
                            "release_windows": [{"start": "09:00", "end": "15:00", "share": 1.0}],
                            "lead_time_mix": [{"days": 5, "share": 1.0}],
                            "material_mix": [{"material_id": "MA025", "share": 1.0}],
                        },
                    ],
                }
            ),
        ]
    )

    criteria = RealismCompiler(config=config, client=client, cache_dir=tmp_path, max_retries=1).compile()

    assert len(criteria.demand_releases) == 10_000
    assert len(client.prompts) == 5
    assert criteria.demand_releases[0].case_id == "C001"
    assert criteria.demand_releases[-1].case_id == "C10000"
    assert criteria.demand_releases == sorted(criteria.demand_releases, key=lambda item: item.release_time)
    assert {release.requested_delivery_date for release in criteria.demand_releases[:6000]} == {
        config.run_settings.run_start_date.replace(day=23)
    }


def test_realism_compiler_rejects_invalid_horizon_pattern_share(tmp_path: Path) -> None:
    config = _load_config(tmp_path, _base_config())
    client = FakeRealismClient(
        [
            _actor_response("procurement_01", 1.2),
            _actor_response("warehouse_01", 1.4),
            _actor_response("accounts_payable_01", 1.0),
            _price_anchor_response("MA025", 20.0),
            json.dumps(
                {
                    "patterns": [
                        {
                            "date": "2026-05-18",
                            "case_count": 2,
                            "workload_intensity": "normal",
                            "release_windows": [{"start": "08:00", "end": "10:00", "share": 0.4}],
                            "lead_time_mix": [{"days": 5, "share": 1.0}],
                            "material_mix": [{"material_id": "MA025", "share": 1.0}],
                        }
                    ],
                }
            ),
        ]
    )

    with pytest.raises(TraceGenerationError, match="release_windows shares must sum to 1.0"):
        RealismCompiler(config=config, client=client, cache_dir=tmp_path, max_retries=1).compile()


def test_actor_day_profiles_vary_by_day_and_workload(tmp_path: Path) -> None:
    config = _load_config(tmp_path, _base_config())
    client = FakeRealismClient(
        [
            _actor_response("procurement_01", 1.2),
            _actor_response("warehouse_01", 1.4),
            _actor_response("accounts_payable_01", 1.0),
            _price_anchor_response("MA025", 20.0),
            json.dumps(
                {
                    "patterns": [
                        {
                            "date": "2026-05-18",
                            "case_count": 1,
                            "workload_intensity": "high",
                            "release_windows": [{"start": "08:00", "end": "09:00", "share": 1.0}],
                            "lead_time_mix": [{"days": 5, "share": 1.0}],
                            "material_mix": [{"material_id": "MA025", "share": 1.0}],
                        },
                        {
                            "date": "2026-05-19",
                            "case_count": 1,
                            "workload_intensity": "low",
                            "release_windows": [{"start": "08:00", "end": "09:00", "share": 1.0}],
                            "lead_time_mix": [{"days": 5, "share": 1.0}],
                            "material_mix": [{"material_id": "MA025", "share": 1.0}],
                        },
                    ],
                }
            ),
        ]
    )

    criteria = RealismCompiler(config=config, client=client, cache_dir=tmp_path, max_retries=1).compile()
    first = criteria.actor_day_profiles[("procurement_01", "2026-05-18")]
    second = criteria.actor_day_profiles[("procurement_01", "2026-05-19")]
    guardrails = config.actors[0].realism_guardrails

    assert first.delay_multiplier != second.delay_multiplier
    assert first.delay_multiplier >= criteria.actor_criteria["procurement_01"].delay_multiplier
    assert guardrails.delay_multiplier_min <= first.delay_multiplier <= guardrails.delay_multiplier_max
    assert guardrails.delay_multiplier_min <= second.delay_multiplier <= guardrails.delay_multiplier_max


def test_env_file_loader_preserves_existing_environment(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                "REALISM_LLM_BASE_URL=http://from-file",
                "REALISM_LLM_MODEL='model-from-file'",
                "export REALISM_LLM_API_KEY=\"token-from-file\"",
            ]
        ),
        encoding="utf-8",
    )
    environ = {"REALISM_LLM_BASE_URL": "http://from-shell"}

    values = load_env_file(env_path, environ=environ)

    assert values == {
        "REALISM_LLM_BASE_URL": "http://from-file",
        "REALISM_LLM_MODEL": "model-from-file",
        "REALISM_LLM_API_KEY": "token-from-file",
    }
    assert environ["REALISM_LLM_BASE_URL"] == "http://from-shell"
    assert environ["REALISM_LLM_MODEL"] == "model-from-file"
    assert environ["REALISM_LLM_API_KEY"] == "token-from-file"


def test_read_env_values_missing_file_returns_empty(tmp_path: Path) -> None:
    assert read_env_values(tmp_path / "missing.env") == {}


def test_cli_loads_default_env_file_before_realism_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_dir = tmp_path / "configuration"
    build_dir = config_dir / "build"
    build_dir.mkdir(parents=True)
    payload = _base_config()
    payload["runSettings"]["realism"] = {"enabled": True, "maxRetries": 1, "cacheDir": str(build_dir)}
    config_path = build_dir / "main.yaml"
    config_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    (config_dir / ".env").write_text(
        "\n".join(
            [
                "REALISM_LLM_BASE_URL=http://realism.local",
                "REALISM_LLM_MODEL=realism-model",
            ]
        ),
        encoding="utf-8",
    )
    responses = [
        _actor_response("procurement_01", 1.2),
        _actor_response("warehouse_01", 1.4),
        _actor_response("accounts_payable_01", 1.0),
        _price_anchor_response("MA025", 20.0),
        json.dumps(
            {
                "patterns": [
                    {
                        "date": "2026-05-18",
                        "case_count": 2,
                        "workload_intensity": "normal",
                        "release_windows": [{"start": "08:05", "end": "08:45", "share": 1.0}],
                        "lead_time_mix": [{"days": 5, "share": 1.0}],
                        "material_mix": [{"material_id": "MA025", "share": 1.0}],
                    }
                ],
            }
        ),
    ]

    class EnvCheckingClient:
        def __init__(self) -> None:
            assert os.environ["REALISM_LLM_BASE_URL"] == "http://realism.local"
            assert os.environ["REALISM_LLM_MODEL"] == "realism-model"

        def complete_json(self, prompt: str) -> str:
            if not responses:
                raise AssertionError("unexpected LLM call")
            return responses.pop(0)

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("REALISM_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("REALISM_LLM_MODEL", raising=False)
    monkeypatch.setattr(realism_module, "OpenAICompatibleLLMClient", EnvCheckingClient)

    exit_code = main(
        [
            str(config_path),
            "--out-dir",
            str(tmp_path / "out"),
            "--run-id",
            "RUN_ENV_REALISM",
        ]
    )

    assert exit_code == 0
    assert responses == []


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
            "day_delay_multiplier_variance": 0.1,
            "day_workday_deviation_hours_variance": 0.2,
            "day_pause_duration_minutes_variance": 10,
            "workload_delay_multiplier_boost": 0.15,
            "workload_workday_deviation_hours_boost": 0.25,
        }
    )


def _price_anchor_response(material_id: str, anchor_price: float) -> str:
    return json.dumps(
        {
            "material_prices": [
                {
                    "material_id": material_id,
                    "anchor_price": anchor_price,
                    "typical_variation_pct": 0.02,
                    "daily_trend_pct": 0.001,
                }
            ]
        }
    )
