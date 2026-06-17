from __future__ import annotations

import pytest

from erp_trace_executor.errors import ToolExecutionError
from erp_trace_executor.fiori_messages import FioriMessageHandler, FioriMessagePolicy


def test_policy_captures_transient_validation_error_without_failing():
    page = FakeMessagePage(messages=[("error", "Geben Sie ein Rechnungsdatum ein.")])
    captured: list[dict[str, str]] = []

    FioriMessageHandler(page, message_sink=captured, policy=FioriMessagePolicy()).handle()

    assert captured == [
        {
            "severity": "error",
            "text": "Geben Sie ein Rechnungsdatum ein.",
            "source": "sap-message-popover",
            "url": "https://sap.example.test/invoice",
        }
    ]
    assert page.clicks == ["role:button:Schließen"]


def test_policy_fails_configured_fatal_message_after_capture_and_dismissal():
    page = FakeMessagePage(messages=[("error", "App konnte wegen technischem Fehler nicht geöffnet werden.")])
    captured: list[dict[str, str]] = []
    policy = FioriMessagePolicy(fatal_patterns=(r"technischem Fehler",))

    with pytest.raises(ToolExecutionError, match="technischem Fehler"):
        FioriMessageHandler(page, message_sink=captured, policy=policy).handle()

    assert captured[0]["text"] == "App konnte wegen technischem Fehler nicht geöffnet werden."
    assert page.clicks == ["role:button:Schließen"]


def test_handler_deduplicates_repeated_messages():
    page = FakeMessagePage(messages=[("error", "Saldo ist ungleich null: 200.00- Soll: 200.00 Haben: 0.00")])
    captured: list[dict[str, str]] = []
    handler = FioriMessageHandler(page, message_sink=captured, policy=FioriMessagePolicy())

    handler.handle()
    handler.handle()

    assert len(captured) == 1


class FakeMessagePage:
    url = "https://sap.example.test/invoice"

    def __init__(self, *, messages: list[tuple[str, str]]) -> None:
        self.messages = messages
        self.clicks: list[str] = []
        self.keyboard = FakeKeyboard(self)

    def evaluate(self, _script: str):
        return [
            {
                "severity": severity,
                "text": text,
                "source": "sap-message-popover",
            }
            for severity, text in self.messages
        ]

    def get_by_role(self, role: str, *, name):
        return FakeMessageLocator(self, f"role:{role}:{name}")

    def get_by_title(self, title: str):
        return FakeMessageLocator(self, f"title:{title}")

    def locator(self, selector: str):
        return FakeMessageLocator(self, f"locator:{selector}")


class FakeKeyboard:
    def __init__(self, page: FakeMessagePage) -> None:
        self._page = page

    def press(self, key: str) -> None:
        self._page.clicks.append(f"keyboard:{key}")


class FakeMessageLocator:
    def __init__(self, page: FakeMessagePage, name: str) -> None:
        self._page = page
        self._name = name

    @property
    def first(self):
        return self

    def click(self, **_kwargs: object) -> None:
        if self._name == "role:button:Schließen":
            self._page.clicks.append(self._name)
            return
        raise RuntimeError(f"not clickable: {self._name}")
