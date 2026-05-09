from __future__ import annotations

from types import SimpleNamespace

from erp_trace_executor.tools.fiori.create_goods_receipt import (
    CreateGoodsReceiptInput,
    SapGoodsReceiptFlow,
    run_create_goods_receipt,
)
from erp_trace_executor.tools.fiori.create_purchase_order import (
    CreatePurchaseOrderInput,
    SapPurchaseOrderFlow,
    run_create_purchase_order,
)
from erp_trace_executor.tools.fiori.create_supplier_invoice import (
    CreateSupplierInvoiceInput,
    SapSupplierInvoiceFlow,
    run_create_supplier_invoice,
)
from erp_trace_executor.tools.fiori.send_payment import (
    SapSendPaymentFlow,
    SendPaymentInput,
    run_send_payment,
)


class FakeContext:
    def __init__(self, tool: str) -> None:
        self.record = SimpleNamespace(task_id="task-1", session_id="session-1", tool=tool)
        self._page = SimpleNamespace(url="https://example.test/fiori")

    def get_fiori_page(self):
        return self._page


def test_purchase_order_result_includes_returned_object(monkeypatch):
    monkeypatch.setattr(
        SapPurchaseOrderFlow,
        "create",
        lambda self, params: {
            "purchase_order": "4500008732",
            "purchase_requisition": params.purchase_requisition,
            "storage_location": params.storage_location,
            "supplier": params.supplier,
            "quantity": params.quantity,
            "tax_code": params.tax_code,
        },
    )

    result = run_create_purchase_order(
        FakeContext("fiori.create_purchase_order"),
        CreatePurchaseOrderInput(
            purchase_requisition="10000030",
            storage_location="FG00",
            supplier="107902",
            quantity=10,
        ),
    )

    assert result.data["purchase_order"] == "4500008732"
    assert result.data["returned_objects"] == [
        {
            "object_type": "purchase_order",
            "keys": {
                "po_number": "4500008732",
            },
        }
    ]


def test_goods_receipt_result_includes_returned_object(monkeypatch):
    monkeypatch.setattr(
        SapGoodsReceiptFlow,
        "create",
        lambda self, params: {
            "material_document": "5000001234",
            "purchase_order": params.purchase_order,
            "document_date": params.document_date,
            "posting_date": params.posting_date,
            "storage_location": params.storage_location,
        },
    )

    result = run_create_goods_receipt(
        FakeContext("fiori.create_goods_receipt"),
        CreateGoodsReceiptInput(
            purchase_order="4500008732",
            document_date="05/14/2026",
            posting_date="05/14/2026",
            storage_location="Trading Goods",
        ),
    )

    assert result.data["material_document"] == "5000001234"
    assert result.data["returned_objects"] == [
        {
            "object_type": "material_document",
            "keys": {
                "material_document_number": "5000001234",
            },
        }
    ]


def test_supplier_invoice_result_includes_returned_object(monkeypatch):
    monkeypatch.setattr(
        SapSupplierInvoiceFlow,
        "create",
        lambda self, params: {
            "supplier_invoice": "5105600001",
            "fiscal_year": "2026",
            "invoice_date": params.invoice_date,
            "invoicing_party": params.invoicing_party,
            "gross_amount": params.gross_amount,
            "purchase_order": params.purchase_order,
            "tax_code": params.tax_code,
        },
    )

    result = run_create_supplier_invoice(
        FakeContext("fiori.create_supplier_invoice"),
        CreateSupplierInvoiceInput(
            invoice_date="05/14/2026",
            invoicing_party="107902",
            gross_amount=1976,
            purchase_order="4500008732",
        ),
    )

    assert result.data["supplier_invoice"] == "5105600001"
    assert result.data["returned_objects"] == [
        {
            "object_type": "supplier_invoice",
            "keys": {
                "invoice_number": "5105600001",
                "fiscal_year": "2026",
            },
        }
    ]


def test_send_payment_result_includes_returned_object(monkeypatch):
    monkeypatch.setattr(
        SapSendPaymentFlow,
        "create",
        lambda self, params: {
            "payment_document": "1500000004",
            "company_code": params.company_code,
            "posting_document_date": params.posting_document_date,
            "posting_date": params.posting_date or "",
            "supplier": params.supplier,
            "accounting_document": params.accounting_document,
            "general_ledger_account": params.general_ledger_account,
            "amount": params.amount,
            "currency": params.currency,
        },
    )

    result = run_send_payment(
        FakeContext("fiori.send_payment"),
        SendPaymentInput(
            company_code="US00",
            posting_document_date="05/09/2026",
            supplier="107902",
            accounting_document="5105600103",
            general_ledger_account="1800000",
            amount=1976,
        ),
    )

    assert result.data["payment_document"] == "1500000004"
    assert result.data["accounting_document"] == "5105600103"
    assert result.data["returned_objects"] == [
        {
            "object_type": "payment_document",
            "keys": {
                "payment_document_number": "1500000004",
            },
        }
    ]
