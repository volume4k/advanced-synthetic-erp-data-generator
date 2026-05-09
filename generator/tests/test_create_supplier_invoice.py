from __future__ import annotations

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from erp_trace_executor.tools.fiori.create_supplier_invoice import (
    SUPPLIER_INVOICE_READY_POLL_MS,
    SUPPLIER_INVOICE_READY_TIMEOUT_MS,
    SapSupplierInvoiceFlow,
)


class FakeSupplierInvoicePage:
    def __init__(self, *, draft_visible: bool, form_visible: bool) -> None:
        self.draft_visible = draft_visible
        self.form_visible = form_visible
        self.waits: list[tuple[str, str, int | None]] = []
        self.clicks: list[str] = []

    def get_by_text(self, text: str):
        return FakeSupplierInvoiceLocator(self, f"text:{text}", visible=self.draft_visible)

    def get_by_role(self, role: str, *, name: str):
        visible = name != "Rechnungsdatum" or self.form_visible
        return FakeSupplierInvoiceLocator(self, f"role:{role}:{name}", visible=visible)


class FakeSupplierInvoiceLocator:
    def __init__(self, page: FakeSupplierInvoicePage, name: str, *, visible: bool) -> None:
        self._page = page
        self._name = name
        self._visible = visible

    @property
    def first(self):
        return self

    def wait_for(self, *, state: str, timeout: int | None = None) -> None:
        self._page.waits.append((self._name, state, timeout))
        if not self._visible:
            raise PlaywrightTimeoutError("not visible")

    def click(self) -> None:
        self._page.clicks.append(self._name)


def test_supplier_invoice_draft_dialog_waits_for_slow_app_load_and_dismisses():
    page = FakeSupplierInvoicePage(draft_visible=True, form_visible=True)

    SapSupplierInvoiceFlow(page)._discard_existing_draft_if_present(page)

    assert ("text:Rechnungsentwurf vorhanden", "visible", SUPPLIER_INVOICE_READY_POLL_MS) in page.waits
    assert page.clicks == ["role:button:Nein"]
    assert ("role:textbox:Rechnungsdatum", "visible", SUPPLIER_INVOICE_READY_TIMEOUT_MS) in page.waits


def test_supplier_invoice_form_ready_keeps_existing_flow_without_waiting_for_draft_timeout():
    page = FakeSupplierInvoicePage(draft_visible=False, form_visible=True)

    SapSupplierInvoiceFlow(page)._discard_existing_draft_if_present(page)

    assert page.clicks == []
    assert ("role:textbox:Rechnungsdatum", "visible", SUPPLIER_INVOICE_READY_POLL_MS) in page.waits
