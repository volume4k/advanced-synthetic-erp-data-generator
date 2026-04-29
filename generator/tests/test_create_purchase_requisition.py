from __future__ import annotations

import pytest
from pydantic import ValidationError

from erp_trace_executor.tools.fiori.create_purchase_requisition import (
    CreatePurchaseRequisitionInput,
    SapPurchaseRequisitionFlow,
)


def _valid_payload() -> dict[str, object]:
    return {
        "material": "PUMP1902",
        "quantity": 20,
        "valuation_price": 30,
        "currency": "USD",
        "price_unit": 1,
        "delivery_date": "20.05.2026",
        "plant": "MI00",
        "purchasing_group": "N00",
        "purchasing_organization": "US00",
        "company_code": "US00",
    }


def test_create_purchase_requisition_input_accepts_required_fields():
    params = CreatePurchaseRequisitionInput.model_validate(_valid_payload())

    assert params.material == "PUMP1902"
    assert params.quantity == 20
    assert params.valuation_price == 30
    assert params.currency == "USD"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("quantity", 0),
        ("valuation_price", 0),
        ("price_unit", 0),
    ],
)
def test_create_purchase_requisition_input_rejects_non_positive_numbers(field: str, value: object):
    payload = _valid_payload()
    payload[field] = value

    with pytest.raises(ValidationError):
        CreatePurchaseRequisitionInput.model_validate(payload)


class FakeLocator:
    def __init__(self, page: "FakeRecordedPage", name: str) -> None:
        self._page = page
        self._name = name

    def click(self) -> None:
        self._page.actions.append(("click", self._name))

    def dblclick(self) -> None:
        self._page.actions.append(("dblclick", self._name))

    def fill(self, value: str) -> None:
        self._page.actions.append(("fill", self._name, value))

    def press(self, key: str) -> None:
        self._page.actions.append(("press", self._name, key))

    def wait_for(self, *, state: str) -> None:
        self._page.actions.append(("wait_for", self._name, state))

    def inner_text(self) -> str:
        return "10000001"


class FakeRecordedPage:
    url = "https://a04p.ucc.cloud/sap/bc/ui2/flp?sap-client=204"

    def __init__(self) -> None:
        self.actions: list[tuple[str, ...]] = []

    def get_by_role(self, role: str, *, name: str, exact: bool | None = None) -> FakeLocator:
        exact_marker = " exact" if exact else ""
        return FakeLocator(self, f"role:{role}:{name}{exact_marker}")

    def get_by_text(self, text: str) -> FakeLocator:
        return FakeLocator(self, f"text:{text}")

    def get_by_label(self, text: str) -> FakeLocator:
        return FakeLocator(self, f"label:{text}")

    def get_by_title(self, title: str, *, exact: bool | None = None) -> FakeLocator:
        exact_marker = " exact" if exact else ""
        return FakeLocator(self, f"title:{title}{exact_marker}")

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator(self, f"locator:{selector}")


def test_sap_purchase_requisition_flow_uses_recorded_steps_and_input_values():
    page = FakeRecordedPage()
    params = CreatePurchaseRequisitionInput.model_validate(_valid_payload())

    data = SapPurchaseRequisitionFlow(page).create(params)

    assert ("fill", "role:searchbox:Suchen", "Bestellanforderung anle") in page.actions
    assert ("fill", "role:textbox:Material exact", "PUMP1902") in page.actions
    assert ("fill", "role:textbox:Bewertungspreis exact", "30") in page.actions
    assert ("fill", "role:textbox:Währung Bewertungspreis", "USD") in page.actions
    assert ("fill", "role:textbox:Preiseinheit", "1") in page.actions
    assert ("fill", "role:textbox:Anforderungsmenge", "20") in page.actions
    assert ("fill", "role:textbox:Lieferdatum", "20.05.2026") in page.actions
    assert ("fill", "role:textbox:Einkäufergruppe", "N00") in page.actions
    assert ("fill", "role:textbox:EinkOrganisation", "US00") in page.actions
    assert ("fill", "role:textbox:Buchungskreis", "US00") in page.actions
    assert ("fill", "role:textbox:Werk", "MI00") in page.actions
    assert page.actions.count(("click", "role:button:Bestellen")) == 2
    assert ("wait_for", "locator:#idPRNoLinkId", "visible") in page.actions
    assert data["purchase_requisition"] == "10000001"
