from __future__ import annotations

from types import SimpleNamespace

from erp_trace_executor import cli
from erp_trace_executor.models import ToolResult


class FakeSessionManager:
    instances: list["FakeSessionManager"] = []

    def __init__(self, *, headless: bool) -> None:
        self.headless = headless
        self.closed = False
        FakeSessionManager.instances.append(self)

    def close(self) -> None:
        self.closed = True


class FakeExecutor:
    should_fail = False
    canonical_calls: list[dict] = []

    def __init__(self, *, credential_store) -> None:
        self.credential_store = credential_store

    def execute_canonical(self, trace, *, init, context_factory, evidence_writer):
        if self.should_fail:
            raise RuntimeError("tool exploded")
        self.canonical_calls.append(
            {
                "trace": trace,
                "init": init,
                "evidence_writer": evidence_writer,
            }
        )
        return [
            ToolResult(
                task_id="canonical-task",
                session_id="session-1",
                tool="fiori.fake",
                data={"status": "canonical-ok"},
            )
        ]


def _patch_cli(monkeypatch) -> None:
    FakeSessionManager.instances = []
    FakeExecutor.should_fail = False
    FakeExecutor.canonical_calls = []
    monkeypatch.setattr(cli, "BrowserSessionManager", FakeSessionManager)
    monkeypatch.setattr(cli, "TraceExecutor", FakeExecutor)
    monkeypatch.setattr(cli, "load_env_credentials", lambda _path: {"credentials": "ok"})


def test_cli_yaml_success_closes_browser(capsys, tmp_path, monkeypatch):
    _patch_cli(monkeypatch)
    trace_path = tmp_path / "RUN_TEST.execution-trace.yaml"
    trace = SimpleNamespace(trace_path=trace_path, run_id="RUN_TEST")
    monkeypatch.setattr(cli, "load_canonical_trace", lambda path: trace)
    monkeypatch.setattr(cli, "read_env_values", lambda _path: {"SAP_USER_1_UN": "BUYER1"})
    monkeypatch.setattr(cli, "build_init_from_sessions", lambda trace, env_values: {"trace": trace, "env": env_values})

    exit_code = cli.main([str(trace_path)])

    assert exit_code == 0
    assert FakeSessionManager.instances[0].closed is True
    assert '"status": "canonical-ok"' in capsys.readouterr().out


def test_cli_rejects_jsonl_trace_format(capsys, monkeypatch):
    _patch_cli(monkeypatch)

    exit_code = cli.main(["trace.jsonl"])

    assert exit_code == 1
    assert FakeSessionManager.instances == []
    assert "Only canonical execution trace YAML files" in capsys.readouterr().err


def test_cli_yaml_path_writes_canonical_artifacts(capsys, tmp_path, monkeypatch):
    _patch_cli(monkeypatch)
    trace_path = tmp_path / "RUN_TEST.execution-trace.yaml"
    artifact_dir = tmp_path / "artifacts"
    trace = SimpleNamespace(trace_path=trace_path, run_id="RUN_TEST")
    monkeypatch.setattr(cli, "load_canonical_trace", lambda path: trace)
    monkeypatch.setattr(cli, "read_env_values", lambda _path: {"SAP_USER_1_UN": "BUYER1"})
    monkeypatch.setattr(cli, "build_init_from_sessions", lambda trace, env_values: {"trace": trace, "env": env_values})

    exit_code = cli.main([str(trace_path), "--artifact-dir", str(artifact_dir)])

    assert exit_code == 0
    assert FakeExecutor.canonical_calls == [
        {
            "trace": trace,
            "init": {"trace": trace, "env": {"SAP_USER_1_UN": "BUYER1"}},
            "evidence_writer": FakeExecutor.canonical_calls[0]["evidence_writer"],
        }
    ]
    assert FakeExecutor.canonical_calls[0]["evidence_writer"].artifact_dir == artifact_dir
    assert '"status": "canonical-ok"' in capsys.readouterr().out


def test_cli_headless_failure_closes_browser_without_waiting(capsys, tmp_path, monkeypatch):
    _patch_cli(monkeypatch)
    FakeExecutor.should_fail = True
    trace_path = tmp_path / "RUN_TEST.execution-trace.yaml"
    trace = SimpleNamespace(trace_path=trace_path, run_id="RUN_TEST")
    monkeypatch.setattr(cli, "load_canonical_trace", lambda path: trace)
    monkeypatch.setattr(cli, "read_env_values", lambda _path: {"SAP_USER_1_UN": "BUYER1"})
    monkeypatch.setattr(cli, "build_init_from_sessions", lambda trace, env_values: {"trace": trace, "env": env_values})
    input_called = False

    def fake_input(_prompt: str) -> str:
        nonlocal input_called
        input_called = True
        return ""

    monkeypatch.setattr(cli, "console_input", fake_input)

    exit_code = cli.main([str(trace_path)])

    assert exit_code == 1
    assert input_called is False
    assert FakeSessionManager.instances[0].closed is True
    assert "RuntimeError: tool exploded" in capsys.readouterr().err


def test_cli_headed_failure_waits_before_closing_browser(capsys, tmp_path, monkeypatch):
    _patch_cli(monkeypatch)
    FakeExecutor.should_fail = True
    trace_path = tmp_path / "RUN_TEST.execution-trace.yaml"
    trace = SimpleNamespace(trace_path=trace_path, run_id="RUN_TEST")
    monkeypatch.setattr(cli, "load_canonical_trace", lambda path: trace)
    monkeypatch.setattr(cli, "read_env_values", lambda _path: {"SAP_USER_1_UN": "BUYER1"})
    monkeypatch.setattr(cli, "build_init_from_sessions", lambda trace, env_values: {"trace": trace, "env": env_values})
    prompts: list[str] = []

    def fake_input(prompt: str) -> str:
        prompts.append(prompt)
        assert FakeSessionManager.instances[0].closed is False
        return ""

    monkeypatch.setattr(cli, "console_input", fake_input)

    exit_code = cli.main([str(trace_path), "--headed"])

    assert exit_code == 1
    assert prompts == ["Press Enter after cleanup to close browser and exit..."]
    assert FakeSessionManager.instances[0].closed is True
    err = capsys.readouterr().err
    assert "Execution failed. Browser remains open for manual SAP cleanup." in err
    assert "RuntimeError: tool exploded" in err


def test_cli_headed_failure_closes_browser_when_prompt_hits_eof(capsys, tmp_path, monkeypatch):
    _patch_cli(monkeypatch)
    FakeExecutor.should_fail = True
    trace_path = tmp_path / "RUN_TEST.execution-trace.yaml"
    trace = SimpleNamespace(trace_path=trace_path, run_id="RUN_TEST")
    monkeypatch.setattr(cli, "load_canonical_trace", lambda path: trace)
    monkeypatch.setattr(cli, "read_env_values", lambda _path: {"SAP_USER_1_UN": "BUYER1"})
    monkeypatch.setattr(cli, "build_init_from_sessions", lambda trace, env_values: {"trace": trace, "env": env_values})

    def fake_input(_prompt: str) -> str:
        assert FakeSessionManager.instances[0].closed is False
        raise EOFError

    monkeypatch.setattr(cli, "console_input", fake_input)

    exit_code = cli.main([str(trace_path), "--headed"])

    assert exit_code == 1
    assert FakeSessionManager.instances[0].closed is True
    assert "RuntimeError: tool exploded" in capsys.readouterr().err
