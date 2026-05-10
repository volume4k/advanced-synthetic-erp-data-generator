from __future__ import annotations

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from erp_trace_executor.tools.fiori.create_supplier_invoice import (
    SUPPLIER_INVOICE_READY_POLL_MS,
    SUPPLIER_INVOICE_READY_TIMEOUT_MS,
    SapSupplierInvoiceFlow,
)


class FakeSupplierInvoicePage:
    def __init__(
        self,
        *,
        draft_visible: bool,
        form_visible: bool,
        draft_visible_after_waits: int | None = None,
        retry_click=None,
    ) -> None:
        self.draft_visible = draft_visible
        self.form_visible = form_visible
        self.draft_visible_after_waits = draft_visible_after_waits
        self._retry_click = retry_click
        self._draft_waits = 0
        self.waits: list[tuple[str, str, int | None]] = []
        self.clicks: list[str] = []

    def get_by_text(self, text: str):
        return FakeSupplierInvoiceLocator(self, f"text:{text}")

    def get_by_role(self, role: str, *, name: str):
        return FakeSupplierInvoiceLocator(self, f"role:{role}:{name}")

    def consume_retryable_click(self):
        retry_click = self._retry_click
        self._retry_click = None
        return retry_click

    def is_visible(self, locator_name: str) -> bool:
        if locator_name == "text:Rechnungsentwurf vorhanden":
            self._draft_waits += 1
            if self.draft_visible_after_waits is not None:
                return self._draft_waits >= self.draft_visible_after_waits
            return self.draft_visible
        if locator_name == "role:textbox:Rechnungsdatum":
            return self.form_visible
        return True


class FakeSupplierInvoiceLocator:
    def __init__(self, page: FakeSupplierInvoicePage, name: str) -> None:
        self._page = page
        self._name = name

    @property
    def first(self):
        return self

    def wait_for(self, *, state: str, timeout: int | None = None) -> None:
        self._page.waits.append((self._name, state, timeout))
        if not self._page.is_visible(self._name):
            raise PlaywrightTimeoutError("not visible")

    def click(self) -> None:
        self._page.clicks.append(self._name)
        if self._name == "role:button:Nein":
            self._page.form_visible = True


def test_supplier_invoice_draft_dialog_waits_for_slow_app_load_and_dismisses():
    page = FakeSupplierInvoicePage(draft_visible=True, form_visible=True)

    SapSupplierInvoiceFlow(page)._discard_existing_draft_if_present(page)

    assert ("text:Rechnungsentwurf vorhanden", "visible", SUPPLIER_INVOICE_READY_POLL_MS) in page.waits
    assert page.clicks == ["role:button:Nein"]
    assert ("role:textbox:Rechnungsdatum", "visible", SUPPLIER_INVOICE_READY_TIMEOUT_MS) in page.waits


def test_supplier_invoice_waits_until_slow_draft_dialog_appears():
    page = FakeSupplierInvoicePage(
        draft_visible=False,
        form_visible=False,
        draft_visible_after_waits=3,
    )

    SapSupplierInvoiceFlow(page)._discard_existing_draft_if_present(page)

    draft_waits = [
        wait for wait in page.waits if wait[0] == "text:Rechnungsentwurf vorhanden"
    ]
    assert len(draft_waits) == 3
    assert page.clicks == ["role:button:Nein"]


def test_supplier_invoice_form_ready_keeps_existing_flow_without_waiting_for_draft_timeout():
    page = FakeSupplierInvoicePage(draft_visible=False, form_visible=True)

    SapSupplierInvoiceFlow(page)._discard_existing_draft_if_present(page)

    assert page.clicks == []
    assert ("role:textbox:Rechnungsdatum", "visible", SUPPLIER_INVOICE_READY_POLL_MS) in page.waits
