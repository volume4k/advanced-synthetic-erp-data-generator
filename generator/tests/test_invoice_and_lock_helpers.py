from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from erp_trace_executor.errors import ToolExecutionError
from erp_trace_executor.tools.fiori.create_goods_receipt import (
    MATERIAL_VALUATION_LOCK_RETRY_DELAY_MS,
    CreateGoodsReceiptInput,
    SapGoodsReceiptFlow,
)
from erp_trace_executor.tools.fiori.create_supplier_invoice import (
    CreateSupplierInvoiceInput,
    SapSupplierInvoiceFlow,
)


def test_supplier_invoice_input_accepts_legacy_gross_amount_alias() -> None:
    params = CreateSupplierInvoiceInput.model_validate(
        {
            "invoice_date": "05/20/2026",
            "invoicing_party": "112800",
            "gross_amount": 123.45,
            "purchase_order": "4500001234",
            "tax_code": "XI",
        }
    )

    assert params.invoice_amount == 123.45


def test_supplier_invoice_input_rejects_conflicting_amount_aliases() -> None:
    with pytest.raises(ValidationError, match="invoice_amount and legacy gross_amount"):
        CreateSupplierInvoiceInput.model_validate(
            {
                "invoice_date": "05/20/2026",
                "invoicing_party": "112800",
                "invoice_amount": 120.0,
                "gross_amount": 123.45,
                "purchase_order": "4500001234",
                "tax_code": "XI",
            }
        )


def test_supplier_invoice_balance_error_fails_with_business_context() -> None:
    params = CreateSupplierInvoiceInput(
        invoice_date="05/20/2026",
        invoicing_party="112800",
        invoice_amount=123.45,
        purchase_order="4500001234",
        tax_code="XI",
    )
    page = SimpleNamespace(
        handle_messages=lambda: [
            SimpleNamespace(text="Saldo ist ungleich null: 123.45- Soll: 123.45 Haben: 0.00")
        ]
    )

    with pytest.raises(ToolExecutionError, match="purchase_order=4500001234.*invoice_amount=123.45.*tax_code=XI"):
        SapSupplierInvoiceFlow(page)._raise_if_balance_not_zero(page, params)


def test_goods_receipt_material_lock_message_extracts_material_and_user() -> None:
    page = SimpleNamespace(
        handle_messages=lambda: [
            SimpleNamespace(text="Bewertungsdaten zum Material CHSP1800 sind von Benutzer LEARN-803 gesperrt")
        ]
    )

    lock = SapGoodsReceiptFlow(page)._material_valuation_lock_message(page)

    assert lock == (
        "CHSP1800",
        "LEARN-803",
        "Bewertungsdaten zum Material CHSP1800 sind von Benutzer LEARN-803 gesperrt",
    )


def test_goods_receipt_material_lock_retry_is_bounded() -> None:
    page = FakeGoodsReceiptPostPage(failures_before_success=2)
    params = CreateGoodsReceiptInput(purchase_order="4500001234", storage_location="Trading Goods")

    material_document = SapGoodsReceiptFlow(page)._post_with_material_lock_retry(page, params)

    assert material_document == "5000012345"
    assert page.post_clicks == 3
    assert page.waits == [
        MATERIAL_VALUATION_LOCK_RETRY_DELAY_MS,
        MATERIAL_VALUATION_LOCK_RETRY_DELAY_MS,
    ]


def test_goods_receipt_material_lock_retry_exhaustion_reports_lock_owner() -> None:
    page = FakeGoodsReceiptPostPage(failures_before_success=99)
    params = CreateGoodsReceiptInput(purchase_order="4500001234", storage_location="Trading Goods")

    with pytest.raises(ToolExecutionError, match="material_id=CHSP1800.*locking_user=LEARN-803.*attempts=3"):
        SapGoodsReceiptFlow(page)._post_with_material_lock_retry(page, params)


class FakeGoodsReceiptPostPage:
    def __init__(self, *, failures_before_success: int) -> None:
        self.failures_before_success = failures_before_success
        self.post_clicks = 0
        self.waits: list[int] = []

    def get_by_role(self, role: str, *, name: str, exact: bool = False):
        assert role == "button"
        assert name == "Buchen"
        assert exact is True
        return FakePostButton(self)

    def locator(self, selector: str, *, has_text: str):
        assert selector == '[role="dialog"]'
        assert has_text == "Materialbeleg"
        return FakeSuccessDialog(self)

    def handle_messages(self):
        return [
            SimpleNamespace(text="Bewertungsdaten zum Material CHSP1800 sind von Benutzer LEARN-803 gesperrt")
        ]

    def wait_for_timeout(self, timeout_ms: int) -> None:
        self.waits.append(timeout_ms)


class FakePostButton:
    def __init__(self, page: FakeGoodsReceiptPostPage) -> None:
        self._page = page

    def click(self) -> None:
        self._page.post_clicks += 1


class FakeSuccessDialog:
    def __init__(self, page: FakeGoodsReceiptPostPage) -> None:
        self._page = page

    @property
    def first(self):
        return self

    def wait_for(self, *, state: str, recover_fiori_messages: bool) -> None:
        assert state == "visible"
        assert recover_fiori_messages is False
        if self._page.post_clicks <= self._page.failures_before_success:
            raise PlaywrightTimeoutError("locked")

    def inner_text(self) -> str:
        return "Materialbeleg 5000012345/2026"
