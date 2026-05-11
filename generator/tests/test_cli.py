from __future__ import annotations

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

    def __init__(self, *, credential_store) -> None:
        self.credential_store = credential_store

    def execute(self, records, context_factory):
        if self.should_fail:
            raise RuntimeError("tool exploded")
        return [
            ToolResult(
                task_id="task-1",
                session_id="session-1",
                tool="fiori.fake",
                data={"status": "ok"},
            )
        ]


def _patch_cli(monkeypatch) -> None:
    FakeSessionManager.instances = []
    FakeExecutor.should_fail = False
    monkeypatch.setattr(cli, "BrowserSessionManager", FakeSessionManager)
    monkeypatch.setattr(cli, "TraceExecutor", FakeExecutor)
    monkeypatch.setattr(cli, "load_trace_records", lambda _path: ["record"])
    monkeypatch.setattr(cli, "load_env_credentials", lambda _path: {"credentials": "ok"})


def test_cli_success_closes_browser(capsys, monkeypatch):
    _patch_cli(monkeypatch)

    exit_code = cli.main(["trace.jsonl"])

    assert exit_code == 0
    assert FakeSessionManager.instances[0].closed is True
    assert '"status": "ok"' in capsys.readouterr().out


def test_cli_headless_failure_closes_browser_without_waiting(capsys, monkeypatch):
    _patch_cli(monkeypatch)
    FakeExecutor.should_fail = True
    input_called = False

    def fake_input(_prompt: str) -> str:
        nonlocal input_called
        input_called = True
        return ""

    monkeypatch.setattr(cli, "console_input", fake_input)

    exit_code = cli.main(["trace.jsonl"])

    assert exit_code == 1
    assert input_called is False
    assert FakeSessionManager.instances[0].closed is True
    assert "RuntimeError: tool exploded" in capsys.readouterr().err


def test_cli_headed_failure_waits_before_closing_browser(capsys, monkeypatch):
    _patch_cli(monkeypatch)
    FakeExecutor.should_fail = True
    prompts: list[str] = []

    def fake_input(prompt: str) -> str:
        prompts.append(prompt)
        assert FakeSessionManager.instances[0].closed is False
        return ""

    monkeypatch.setattr(cli, "console_input", fake_input)

    exit_code = cli.main(["trace.jsonl", "--headed"])

    assert exit_code == 1
    assert prompts == ["Press Enter after cleanup to close browser and exit..."]
    assert FakeSessionManager.instances[0].closed is True
    err = capsys.readouterr().err
    assert "Execution failed. Browser remains open for manual SAP cleanup." in err
    assert "RuntimeError: tool exploded" in err


def test_cli_headed_failure_closes_browser_when_prompt_hits_eof(capsys, monkeypatch):
    _patch_cli(monkeypatch)
    FakeExecutor.should_fail = True

    def fake_input(_prompt: str) -> str:
        assert FakeSessionManager.instances[0].closed is False
        raise EOFError

    monkeypatch.setattr(cli, "console_input", fake_input)

    exit_code = cli.main(["trace.jsonl", "--headed"])

    assert exit_code == 1
    assert FakeSessionManager.instances[0].closed is True
    assert "RuntimeError: tool exploded" in capsys.readouterr().err
