from __future__ import annotations

import pytest
from pydantic import ValidationError

from erp_trace_executor.tools.fiori.create_purchase_order import (
    CreatePurchaseOrderInput,
    SapPurchaseOrderFlow,
)


class FakeSapGuiSpanTextbox:
    def __init__(self) -> None:
        self.actions: list[tuple[str, str | None]] = []

    def press(self, key: str) -> None:
        self.actions.append(("press", key))


def test_purchase_order_net_price_is_required():
    with pytest.raises(ValidationError, match="net_price"):
        CreatePurchaseOrderInput.model_validate(
            {
                "purchase_requisition": "10000030",
                "storage_location": "0001",
                "supplier": "107902",
                "quantity": 10,
            }
        )


def test_purchase_order_selected_grid_textbox_types_without_selecting_all():
    cell = FakeSapGuiSpanTextbox()
    flow = SapPurchaseOrderFlow(page=None)

    flow._type_grid_textbox_value(cell, "91.57")

    assert cell.actions == [
        ("press", "9"),
        ("press", "1"),
        ("press", "."),
        ("press", "5"),
        ("press", "7"),
        ("press", "Enter"),
    ]
