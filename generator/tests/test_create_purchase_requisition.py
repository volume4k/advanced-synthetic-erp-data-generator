from __future__ import annotations

from re import Pattern
from typing import Any

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from pydantic import ValidationError

from erp_trace_executor.tools.fiori.create_purchase_requisition import (
    CreatePurchaseRequisitionInput,
    PURCHASE_REQUISITION_DRAFT_GRACE_MS,
    PURCHASE_REQUISITION_READY_POLL_MS,
    PURCHASE_REQUISITION_READY_TIMEOUT_MS,
    SapPurchaseRequisitionFlow,
)
from erp_trace_executor.fiori_page import FioriPage


def _valid_payload() -> dict[str, object]:
    return {
        "material": "PUMP1902",
        "quantity": 20,
        "valuation_price": 30,
        "currency": "USD",
        "price_unit": 1,
        "delivery_date": "05/20/2026",
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

    def wait_for(self, *, state: str, timeout: int | None = None) -> None:
        self._page.actions.append(("wait_for", self._name, state, timeout))
        if self._name == "text:Entwurf der Bestellanforderung":
            raise PlaywrightTimeoutError("not visible")

    def inner_text(self) -> str:
        return "10000001"

    @property
    def first(self):
        return self


class FakeRecordedPage:
    url = "https://a04p.ucc.cloud/sap/bc/ui2/flp?sap-client=204"

    def __init__(self) -> None:
        self.actions: list[tuple[Any, ...]] = []

    def get_by_role(self, role: str, *, name: str | Pattern[str], exact: bool | None = None) -> FakeLocator:
        exact_marker = " exact" if exact else ""
        locator_name = name.pattern if isinstance(name, Pattern) else name
        return FakeLocator(self, f"role:{role}:{locator_name}{exact_marker}")

    def get_by_text(self, text: str) -> FakeLocator:
        return FakeLocator(self, f"text:{text}")

    def get_by_label(self, text: str) -> FakeLocator:
        return FakeLocator(self, f"label:{text}")

    def get_by_title(self, title: str, *, exact: bool | None = None) -> FakeLocator:
        exact_marker = " exact" if exact else ""
        return FakeLocator(self, f"title:{title}{exact_marker}")

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator(self, f"locator:{selector}")

    def wait_for_load_state(self, state: str, *, timeout: int) -> None:
        self.actions.append(("wait_for_load_state", state, timeout))

    def wait_for_function(self, expression: str, **kwargs: Any) -> None:
        self.actions.append(("wait_for_function", "quietMs" in str(kwargs.get("arg")), kwargs.get("timeout")))


def test_sap_purchase_requisition_flow_uses_recorded_steps_and_input_values():
    page = FakeRecordedPage()
    params = CreatePurchaseRequisitionInput.model_validate(_valid_payload())

    data = SapPurchaseRequisitionFlow(FioriPage(page)).create(params)

    assert ("fill", "role:searchbox:Suchen", "Bestellanforderung anle") in page.actions
    assert ("wait_for", "role:textbox:Material", "visible", 3000) in page.actions
    assert ("fill", "role:textbox:Material", "PUMP1902") in page.actions
    assert ("fill", "role:textbox:Bewertungspreis exact", "30") in page.actions
    assert ("fill", "role:textbox:Währung Bewertungspreis", "USD") in page.actions
    assert ("fill", "role:textbox:Preiseinheit", "1") in page.actions
    assert ("fill", "role:textbox:Anforderungsmenge", "20") in page.actions
    assert ("fill", "role:textbox:Lieferdatum", "05/20/2026") in page.actions
    assert ("fill", "role:textbox:Einkäufergruppe", "N00") in page.actions
    assert ("fill", "role:textbox:EinkOrganisation", "US00") in page.actions
    assert ("fill", "role:textbox:Buchungskreis", "US00") in page.actions
    assert ("fill", "role:textbox:Werk", "MI00") in page.actions
    assert page.actions.count(("click", "role:button:Bestellen")) == 1
    assert ("wait_for", "locator:#idPRNoLinkId", "visible", None) in page.actions
    assert data["purchase_requisition"] == "10000001"


class FakePurchaseRequisitionDraftPage:
    def __init__(
        self,
        *,
        draft_visible: bool,
        form_visible: bool,
        draft_visible_after_waits: int | None = None,
        position_visible_while_draft_pending: bool = False,
    ) -> None:
        self.draft_visible = draft_visible
        self.form_visible = form_visible
        self.draft_visible_after_waits = draft_visible_after_waits
        self.position_visible_while_draft_pending = position_visible_while_draft_pending
        self._draft_waits = 0
        self.waits: list[tuple[str, str, int | None]] = []
        self.clicks: list[str] = []

    def get_by_text(self, text: str):
        return FakePurchaseRequisitionDraftLocator(self, f"text:{text}")

    def get_by_role(self, role: str, *, name: str, exact: bool | None = None):
        exact_marker = " exact" if exact else ""
        return FakePurchaseRequisitionDraftLocator(self, f"role:{role}:{name}{exact_marker}")

    def is_visible(self, locator_name: str) -> bool:
        if locator_name == "text:Entwurf der Bestellanforderung":
            self._draft_waits += 1
            if self.draft_visible_after_waits is not None:
                return self._draft_waits >= self.draft_visible_after_waits
            return self.draft_visible
        if locator_name == "role:button:Position anlegen exact":
            if self.position_visible_while_draft_pending:
                return True
            return self.form_visible
        return True


class FakePurchaseRequisitionDraftLocator:
    def __init__(self, page: FakePurchaseRequisitionDraftPage, name: str) -> None:
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
        if self._name == "role:button:Verwerfen":
            self._page.form_visible = True


def test_purchase_requisition_discards_existing_draft_dialog():
    page = FakePurchaseRequisitionDraftPage(draft_visible=True, form_visible=False)

    SapPurchaseRequisitionFlow(page)._discard_existing_draft_if_present(page)

    assert ("text:Entwurf der Bestellanforderung", "visible", PURCHASE_REQUISITION_READY_POLL_MS) in page.waits
    assert page.clicks == ["role:button:Verwerfen"]
    assert (
        "role:button:Position anlegen exact",
        "visible",
        PURCHASE_REQUISITION_READY_TIMEOUT_MS,
    ) in page.waits


def test_purchase_requisition_form_ready_keeps_existing_flow_without_draft_click():
    page = FakePurchaseRequisitionDraftPage(draft_visible=False, form_visible=True)

    SapPurchaseRequisitionFlow(page)._discard_existing_draft_if_present(page)

    assert page.clicks == []
    assert not any(wait[0] == "role:button:Position anlegen exact" for wait in page.waits)


def test_purchase_requisition_waits_until_slow_draft_dialog_appears():
    page = FakePurchaseRequisitionDraftPage(
        draft_visible=False,
        form_visible=False,
        draft_visible_after_waits=3,
    )

    SapPurchaseRequisitionFlow(page)._discard_existing_draft_if_present(page)

    draft_waits = [
        wait for wait in page.waits if wait[0] == "text:Entwurf der Bestellanforderung"
    ]
    assert len(draft_waits) == 3
    assert page.clicks == ["role:button:Verwerfen"]


def test_purchase_requisition_does_not_treat_visible_position_button_as_no_draft():
    page = FakePurchaseRequisitionDraftPage(
        draft_visible=False,
        form_visible=False,
        draft_visible_after_waits=3,
        position_visible_while_draft_pending=True,
    )

    SapPurchaseRequisitionFlow(page)._discard_existing_draft_if_present(page)

    draft_waits = [
        wait for wait in page.waits if wait[0] == "text:Entwurf der Bestellanforderung"
    ]
    assert len(draft_waits) == 3
    assert all(wait[2] <= PURCHASE_REQUISITION_DRAFT_GRACE_MS for wait in draft_waits)
    assert page.clicks == ["role:button:Verwerfen"]


class FakePurchaseRequisitionPositionPage:
    def __init__(self) -> None:
        self.position_clicks = 0
        self.material_waits = 0
        self.discarded = False
        self.clicks: list[str] = []
        self.waits: list[tuple[str, str, int | None]] = []

    def get_by_text(self, text: str):
        return FakePurchaseRequisitionPositionLocator(self, f"text:{text}")

    def get_by_role(self, role: str, *, name: str | Pattern[str], exact: bool | None = None):
        exact_marker = " exact" if exact else ""
        locator_name = name.pattern if isinstance(name, Pattern) else name
        return FakePurchaseRequisitionPositionLocator(self, f"role:{role}:{locator_name}{exact_marker}")

    def is_visible(self, locator_name: str) -> bool:
        if locator_name == "text:Entwurf der Bestellanforderung":
            return self.material_waits > 0 and not self.discarded
        if locator_name == "role:button:Position anlegen exact":
            return True
        if locator_name == "role:textbox:Material":
            self.material_waits += 1
            return self.discarded
        return True


class FakePurchaseRequisitionPositionLocator:
    def __init__(self, page: FakePurchaseRequisitionPositionPage, name: str) -> None:
        self._page = page
        self._name = name

    @property
    def first(self):
        return self

    def click(self, **_kwargs: object) -> None:
        self._page.clicks.append(self._name)
        if self._name == "role:button:Position anlegen exact":
            self._page.position_clicks += 1
        if self._name == "role:button:Verwerfen":
            self._page.discarded = True

    def wait_for(
        self,
        *,
        state: str,
        timeout: int | None = None,
        recover_fiori_messages: bool | None = None,
    ) -> None:
        self._page.waits.append((self._name, state, timeout))
        if not self._page.is_visible(self._name):
            raise PlaywrightTimeoutError("not visible")


def test_purchase_requisition_discards_draft_when_material_field_wait_fails():
    page = FakePurchaseRequisitionPositionPage()

    material_field = SapPurchaseRequisitionFlow(page)._open_new_position(page)

    assert material_field._name == "role:textbox:Material"
    assert page.clicks == [
        "role:button:Position anlegen exact",
        "role:button:Verwerfen",
        "role:button:Position anlegen exact",
    ]
    assert page.material_waits == 2
