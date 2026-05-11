from __future__ import annotations

from erp_trace_executor.tools.fiori.create_purchase_order import SapPurchaseOrderFlow


class FakeSapGuiSpanTextbox:
    def __init__(self) -> None:
        self.actions: list[tuple[str, str | None]] = []

    def click(self) -> None:
        self.actions.append(("click", None))

    def fill(self, _value: str) -> None:
        raise AssertionError("SAP GUI span textboxes are not fillable")

    def press(self, key: str) -> None:
        self.actions.append(("press", key))


def test_purchase_order_grid_textbox_replaces_value_without_fill():
    cell = FakeSapGuiSpanTextbox()
    flow = SapPurchaseOrderFlow(page=None)

    flow._replace_grid_textbox_value(cell, "10")

    assert cell.actions == [
        ("click", None),
        ("press", "ControlOrMeta+a"),
        ("press", "1"),
        ("press", "0"),
        ("press", "Enter"),
    ]
