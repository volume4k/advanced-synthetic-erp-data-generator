"""Central SAP Fiori message capture and dismissal."""

from __future__ import annotations

import re
from collections.abc import MutableSequence
from dataclasses import dataclass
from typing import Any

from playwright.sync_api import Error as PlaywrightError

from erp_trace_executor.errors import ToolExecutionError


DEFAULT_FATAL_MESSAGE_PATTERNS = (
    r"session.*abgelaufen",
    r"sitzung.*abgelaufen",
    r"not authorized",
    r"nicht berechtigt",
    r"could not be opened",
    r"konnte.*nicht.*geöffnet",
    r"ui5 component.*could not be loaded",
    r"technischer fehler",
    r"technical error",
)


@dataclass(frozen=True)
class FioriMessage:
    """One captured SAP Fiori message."""

    severity: str
    text: str
    source: str
    url: str
    details: str | None = None

    def key(self) -> tuple[str, str, str]:
        return (self.severity, self.text, self.source)

    def to_dict(self) -> dict[str, str]:
        payload = {
            "severity": self.severity,
            "text": self.text,
            "source": self.source,
            "url": self.url,
        }
        if self.details:
            payload["details"] = self.details
        return payload


class FioriMessagePolicy:
    """Classify captured SAP messages."""

    def __init__(self, fatal_patterns: tuple[str, ...] = DEFAULT_FATAL_MESSAGE_PATTERNS) -> None:
        self._fatal_patterns = tuple(re.compile(pattern, re.IGNORECASE) for pattern in fatal_patterns)

    def is_fatal(self, message: FioriMessage) -> bool:
        return any(pattern.search(message.text) for pattern in self._fatal_patterns)


class FioriMessageHandler:
    """Capture and dismiss global SAP Fiori message surfaces."""

    def __init__(
        self,
        page: Any,
        *,
        message_sink: MutableSequence[dict[str, str]] | None = None,
        policy: FioriMessagePolicy | None = None,
    ) -> None:
        self._page = page
        self._message_sink = message_sink
        self._policy = policy or FioriMessagePolicy()
        self._seen: set[tuple[str, str, str]] = set()

    def handle(self) -> list[FioriMessage]:
        """Capture messages, close blocking overlays, and fail only fatal text."""

        raw_messages = self._collect_visible_messages()
        messages = [self._message_from_raw(raw) for raw in raw_messages if self._message_text(raw)]
        new_messages = [message for message in messages if message.key() not in self._seen]
        for message in new_messages:
            self._seen.add(message.key())
            if self._message_sink is not None:
                self._message_sink.append(message.to_dict())

        if messages:
            self.dismiss_blocking_messages()

        fatal_messages = [message for message in messages if self._policy.is_fatal(message)]
        if fatal_messages:
            fatal_text = "; ".join(message.text for message in fatal_messages)
            raise ToolExecutionError(f"Fatal SAP Fiori message at {self._url()}: {fatal_text}")

        return messages

    def dismiss_blocking_messages(self) -> None:
        """Best-effort close for popovers/dialogs that can cover later clicks."""

        for locator in self._dismiss_locators():
            try:
                locator.click(timeout=1_000)
                return
            except (PlaywrightError, AttributeError, TypeError, RuntimeError):
                continue

        keyboard = getattr(self._page, "keyboard", None)
        if keyboard is not None:
            try:
                keyboard.press("Escape")
            except (PlaywrightError, AttributeError, TypeError, RuntimeError):
                return

    def _collect_visible_messages(self) -> list[dict[str, str]]:
        try:
            raw_messages = self._page.evaluate(_MESSAGE_SCAN_SCRIPT)
        except (PlaywrightError, AttributeError, TypeError, RuntimeError):
            return []
        if not isinstance(raw_messages, list):
            return []
        return [message for message in raw_messages if isinstance(message, dict)]

    def _message_from_raw(self, raw: dict[str, str]) -> FioriMessage:
        return FioriMessage(
            severity=str(raw.get("severity") or "unknown").lower(),
            text=self._message_text(raw),
            source=str(raw.get("source") or "sap-message"),
            url=self._url(),
            details=str(raw["details"]) if raw.get("details") else None,
        )

    def _message_text(self, raw: dict[str, str]) -> str:
        return " ".join(str(raw.get("text") or "").split())

    def _url(self) -> str:
        return str(getattr(self._page, "url", ""))

    def _dismiss_locators(self) -> list[Any]:
        locators: list[Any] = []
        for name in ("Schließen", "OK", "Close"):
            try:
                locators.append(self._page.get_by_role("button", name=name))
            except (PlaywrightError, AttributeError, TypeError, RuntimeError):
                pass
        for title in ("Entfernen", "Schließen", "Close"):
            try:
                locators.append(self._page.get_by_title(title))
            except (PlaywrightError, AttributeError, TypeError, RuntimeError):
                pass
        for selector in (
            "[id*='messageView'] [title='Schließen']",
            "[id*='messageView'] [title='Close']",
            ".sapMPopover .sapMBtn[title='Schließen']",
            ".sapMPopover .sapMBtn[title='Close']",
        ):
            try:
                locators.append(self._page.locator(selector).first)
            except (PlaywrightError, AttributeError, TypeError, RuntimeError):
                pass
        return locators


_MESSAGE_SCAN_SCRIPT = """
() => {
    const isVisible = (element) => {
        if (!element) return false;
        const style = window.getComputedStyle(element);
        if (style.visibility === "hidden" || style.display === "none") return false;
        return Boolean(element.offsetWidth || element.offsetHeight || element.getClientRects().length);
    };
    const normalizedText = (element) => (element.innerText || element.textContent || "").replace(/\\s+/g, " ").trim();
    const severityFor = (element) => {
        const haystack = [
            element.className,
            element.id,
            element.getAttribute("title"),
            element.getAttribute("aria-label"),
            element.getAttribute("data-sap-ui-icon-content"),
            element.innerHTML
        ].join(" ").toLowerCase();
        if (haystack.includes("error") || haystack.includes("fehler")) return "error";
        if (haystack.includes("warning") || haystack.includes("warnung")) return "warning";
        if (haystack.includes("success") || haystack.includes("erfolg")) return "success";
        if (haystack.includes("information") || haystack.includes("info")) return "information";
        return "unknown";
    };
    const sourceFor = (element) => {
        const haystack = [element.className, element.id, element.getAttribute("role")].join(" ").toLowerCase();
        if (haystack.includes("messageview") || haystack.includes("messagepopover") || haystack.includes("popover")) {
            return "sap-message-popover";
        }
        if (haystack.includes("dialog")) return "sap-message-dialog";
        if (haystack.includes("toast")) return "sap-message-toast";
        if (haystack.includes("messagestrip")) return "sap-message-strip";
        return "sap-message";
    };
    const selectors = [
        "[id*='messageView']",
        "[id*='MessageView']",
        ".sapMMessagePopover",
        ".sapMMsgPopover",
        ".sapMPopover[id*='popover'] [id*='messageView']",
        ".sapMDialog",
        "[role='dialog']",
        ".sapMMessageToast",
        ".sapMMessageStrip",
        ".sapMInputBaseContentWrapperError",
        ".sapMInputBaseContentWrapperWarning",
        "[aria-invalid='true']"
    ];
    const seen = new Set();
    const messages = [];
    for (const element of document.querySelectorAll(selectors.join(","))) {
        if (!isVisible(element)) continue;
        const text = normalizedText(element);
        if (!text) continue;
        const key = `${sourceFor(element)}|${text}`;
        if (seen.has(key)) continue;
        seen.add(key);
        messages.push({
            severity: severityFor(element),
            text,
            source: sourceFor(element)
        });
    }
    return messages;
}
"""
